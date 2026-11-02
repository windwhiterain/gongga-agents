#!/usr/bin/env bash
# Turn uncommitted changes into one public commit that carries exactly the paths
# you name. What "public" means depends on the checkout it finds:
#
#   a plain clone (no branch named `local`)
#       commit those paths on the current branch and push to origin. One step.
#
#   this machine (a `local` branch exists)
#       commit them on the worktree's `pub` at origin/main, push, fast-forward
#       `main`, then rebase `local` onto it -- the checkout the server reads
#       never leaves `local`, so the machine keeps working while it happens.
#
#   bash scripts/publish.sh -m "model_pool: weights" --dry-run
#   bash scripts/publish.sh -m "model_pool: weights"                     # every changed path
#   bash scripts/publish.sh -m "providers: glm" --path providers.toml    # only these paths
#
# Run it with `bash`, never as ./scripts/publish.sh: this machine's git records
# no file modes (core.fileMode=false), so the file is 100644 in every clone.
#
# README.md's 「提交往哪去」 is five commands in three directories, and the one
# checkout that must not move is the one the server is reading. The order is the
# whole correctness argument:
#
#   1. checks   on `local`, nothing in progress, `main` == origin/main, pub clean
#   2. patch    the working tree's changes, new files included, in .git/publish/
#   3. publish  apply that patch on `pub` at origin/main, commit, push
#   4. land     fast-forward `main`, rebase `local`, put back what was not published
#   5. check    README's invariants, or say which one broke
#
# Nothing in the main checkout is written before step 4, so a failure in 2 or 3
# leaves the working tree exactly as it was. A failure in 4 says what to run.
set -Eeuo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/.." && pwd)

repo=${GONGGA_PUBLISH_REPO:-$root}
remote=${GONGGA_PUBLISH_REMOTE:-origin}
branch=${GONGGA_PUBLISH_BRANCH:-main}
pub=${GONGGA_PUBLISH_PUB:-pub}
wt_rel=.worktrees/work

message=""
dry=0
paths=()
patch=""

die() { printf 'publish: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,\} \{0,1\}//'
}

contains() { # contains NEEDLE HAYSTACK...
  local want=$1 needle
  for needle in "${@:2}"; do
    if [ "$needle" = "$want" ]; then return 0; fi
  done
  return 1
}

while [ $# -gt 0 ]; do
  case $1 in
    -m | --message)
      message=${2-}
      if [ -z "$message" ]; then die "-m needs a message"; fi
      shift 2
      ;;
    -p | --path)
      if [ -z "${2-}" ]; then die "--path needs a path"; fi
      paths+=("${2#./}")
      shift 2
      ;;
    --dry-run) dry=1; shift ;;
    --repo)
      if [ -z "${2-}" ]; then die "--repo needs a path"; fi
      repo=$2
      shift 2
      ;;
    --remote)
      if [ -z "${2-}" ]; then die "--remote needs a name"; fi
      remote=$2
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

[ -n "$message" ] || die "say what changed: -m \"providers: ...\" (try --help)"
case $message in
  local:*)
    die "a public commit never starts with 'local:' -- that prefix is the machine's.
  public commits use an area prefix: providers: / model_pool: / README:"
    ;;
esac

repo=$(cd "$repo" 2>/dev/null && pwd) || die "no checkout at $repo"
wt=$repo/$wt_rel
gitq() { git -C "$repo" "$@"; }
gitw() { git -C "$wt" "$@"; }

phase=setup
stop() {
  local code=$1
  printf '\n' >&2
  case $phase in
    patch | publish)
      printf 'publish: stopped before the main checkout changed.\n' >&2
      printf '  your changes are still in the working tree; the patch is %s\n' "${patch:-<none>}" >&2
      ;;
    land)
      printf 'publish: landing did not finish -- the commit IS on %s/%s.\n' "$remote" "$branch" >&2
      printf '  if the main checkout is mid-operation, undo it and land by hand:\n' >&2
      printf '    git -C %s rebase --abort\n' "$repo" >&2
      printf '    git -C %s fetch -q . refs/heads/%s:refs/heads/%s && git -C %s rebase %s\n' \
        "$repo" "$pub" "$branch" "$repo" "$branch" >&2
      ;;
    *)
      printf 'publish: stopped during %s.\n' "$phase" >&2
      ;;
  esac
  exit "$code"
}
trap 'stop $?' ERR

# Leaving the checkout usable is worth more than the exit status: the content is
# on the remote already, and a working tree parked in a conflict is what the
# server would read next.
leave_as_found() {
  say "!! landing did not finish -- the public commit IS on $remote/$branch."
  say "   the main checkout was put back clean, on local, without it."
  say "   land it when you are ready:"
  say "     git -C $repo fetch -q . refs/heads/$pub:refs/heads/$branch"
  say "     git -C $repo rebase $branch"
  say "   (the machine's hourly sync rebases local on its own, so waiting also works)"
  if [ -n "${aside:-}" ]; then
    say "   changes kept aside are still stashed: git -C $repo stash list"
  fi
  exit 1
}

gitq rev-parse --git-dir >/dev/null 2>&1 || die "$repo is not a git checkout"
head=$(gitq rev-parse --abbrev-ref HEAD)

# Two shapes of checkout, and they need different things.
#
#   plain   somebody's clone of the repository: one branch, straight to the
#           remote. Nothing to keep out of the way, so the commit is made here.
#   split   this machine: `local` is read by the server and carries machine
#           commits, `main` is the public line. The commit is made in a worktree
#           so the checkout the server reads never leaves `local`.
if gitq rev-parse -q --verify local >/dev/null; then
  shape=split
else
  shape=plain
fi

if [ "$shape" = plain ] && [ "$head" = local ]; then
  die "this checkout has a branch named local but publish needs it to stay here;
  run this from a clone without one, or say --repo"
fi

if [ "$shape" = split ] && [ ! -d "$wt" ]; then
  # A fresh clone has no worktree yet. Making one does not move this checkout,
  # so it is safe to do here -- and a contributor should not have to read the
  # README twice to publish one change.
  gitq fetch -q "$remote"
  gitq worktree add -q "$wt_rel" -B "$pub" "$remote/$branch" ||
    die "no worktree at $wt_rel, and creating one failed:
  git -C $repo worktree add $wt_rel -B $pub $remote/$branch"
  say "made a worktree at $wt_rel (branch $pub) -- this checkout stayed on local"
fi

if [ "$shape" = split ] && [ "$head" != local ]; then
  die "the main checkout is on '$head', not 'local'.
  the server reads this working tree, so it stays on local:
    git -C $repo checkout local"
fi

gitdir=$(cd "$repo" && cd "$(gitq rev-parse --git-dir)" && pwd)
for mark in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
  if [ -e "$gitdir/$mark" ]; then
    die "$mark is in progress in the main checkout; finish or abort it first"
  fi
done

if [ "$shape" = split ] && ! gitq check-ignore -q "$wt_rel/x" 2>/dev/null; then
  die "$wt_rel is not ignored in this checkout, so it would show up as untracked
  everywhere and this script would keep tripping over it. add it once:
    printf '%s\\n' '$wt_rel/' >> $repo/.gitignore"
fi

if [ "$shape" = split ]; then
  pub_dirty=$(gitw status --porcelain)
  if [ -n "$pub_dirty" ]; then
    die "the worktree $wt_rel has uncommitted changes:
$pub_dirty
  it is a workspace: commit or discard them, then run this again"
  fi
fi

# Read line by line, not with `mapfile`: macOS still ships bash 3.2, and this
# is meant to be run by whoever cloned the repository.
# The worktree this script uses is not content, even in a checkout whose
# .gitignore does not know about it: it is dropped here, before anything can be
# selected, kept aside or stashed.
mine() { case $1 in "$wt_rel" | "$wt_rel"/*) return 1 ;; *) return 0 ;; esac; }
modified=()
while IFS= read -r line; do
  [ -n "$line" ] && mine "$line" && modified+=("$line")
done < <(gitq diff --name-only HEAD)
newer=()
while IFS= read -r line; do
  [ -n "$line" ] && mine "$line" && newer+=("$line")
done < <(gitq ls-files --others --exclude-standard)
all=("${modified[@]}" "${newer[@]}")
if [ ${#all[@]} -eq 0 ]; then die "nothing to publish: the working tree is clean"; fi

if [ ${#paths[@]} -eq 0 ]; then
  sel=("${all[@]}")
else
  sel=()
  for p in "${paths[@]}"; do
    if ! contains "$p" "${all[@]}"; then
      die "not changed here: $p
  changed: ${all[*]}"
    fi
    if ! contains "$p" "${sel[@]}"; then sel+=("$p"); fi
  done
fi

rest=()
for p in "${all[@]}"; do
  if ! contains "$p" "${sel[@]}"; then rest+=("$p"); fi
done
newonly=()
for p in "${sel[@]}"; do
  if contains "$p" "${newer[@]}"; then newonly+=("$p"); fi
done

if [ "$dry" = 1 ]; then
  step "would publish"
  printf '  %s\n' "${sel[@]}"
  step "diff"
  known=()
  for p in "${sel[@]}"; do
    if ! contains "$p" "${newonly[@]}"; then known+=("$p"); fi
  done
  if [ ${#known[@]} -gt 0 ]; then
    gitq --no-pager diff --stat HEAD -- "${known[@]}" | sed 's/^/  /'
  fi
  for p in "${newonly[@]}"; do
    say "  new: $p ($(wc -l <"$repo/$p" 2>/dev/null | tr -d ' ') lines)"
  done
  step "then"
  if [ "$shape" = plain ]; then
    say "  commit $head: \"$message\""
    say "  push it to $remote $head"
  else
    say "  commit on $pub at $remote/$branch: \"$message\""
    say "  push it to $remote $branch, fast-forward $branch, rebase local onto it"
    if [ ${#rest[@]} -gt 0 ]; then
      say "  keep aside and put back after the rebase: ${rest[*]}"
    fi
  fi
  exit 0
fi

for p in "${sel[@]}"; do
  case $p in
    providers.toml | providers/*)
      if gitq diff HEAD -- "${sel[@]}" | grep -qE '^\+.*"(sk|ak|flm_ak|user)_'; then
        die "a key-looking string is in the diff, and this commit becomes public
  (README「怎么贡献」7). look for it, then run this again:
    git -C $repo diff HEAD -- $p | grep -nE '\"(sk|ak|flm_ak|user)_'"
      fi
      ;;
  esac
done

if [ "$shape" = plain ]; then
  # One branch, one remote, no machine content to keep apart: commit here and
  # push. `--only` limits the commit to the selected paths, so anything left in
  # the working tree stays uncommitted and untouched.
  step "commit and push on $head"
  phase=publish
  gitq add -A -- "${sel[@]}"
  if ! gitq commit -q -m "$message" -- "${sel[@]}"; then
    die "the commit did not happen -- the working tree is as it was"
  fi
  # The commit must name exactly the selected paths: nothing else may ride
  # along, and an untouched path must not be in it.
  got=$(gitq show --name-only --pretty=format: HEAD | sed '/^[[:space:]]*$/d' | LC_ALL=C sort | tr '\n' ' ')
  want=$(printf '%s\n' "${sel[@]}" | LC_ALL=C sort | tr '\n' ' ')
  if [ "$got" != "$want" ]; then
    say "!! the commit carries [$got] but these paths changed [$want]"
    say "   undo it with: git -C $repo reset --soft HEAD~1"
    exit 1
  fi
  if ! gitq push -q "$remote" "$head:$head"; then
    say "!! the commit is local; the push was refused (fetch first, then push again)"
    exit 1
  fi
  say "  pushed $(gitq rev-parse --short HEAD) to $remote/$head"
  step "checks"
  left=$(gitq status --short)
  if [ -n "$left" ]; then
    say "  still uncommitted (not published):"
    printf '   %s\n' "$left"
  fi
  say "done: $(gitq rev-parse --short HEAD) on $head ($remote agrees)"
  exit 0
fi

step "the patch"
mkdir -p "$gitdir/publish"
stamp=$(date +%Y%m%d-%H%M%S)
patch=$gitdir/publish/$stamp.diff
# Kept so a failure can be retried by hand, and pruned so a repository that
# publishes for a year does not grow a directory of diffs. It lives under .git,
# which no commit can reach; `docs` says the same thing where a reader looks.
KEEP_PATCHES=${KEEP_PATCHES:-10}
# A file git does not know yet has to be made visible to `diff`, and put back
# afterwards so the tree is left exactly as the operator left it.
phase=patch
if [ ${#newonly[@]} -gt 0 ]; then
  gitq add -N -- "${newonly[@]}"
  if ! gitq diff HEAD --binary -- "${sel[@]}" >"$patch"; then
    gitq reset -q -- "${newonly[@]}"
    die "could not write the patch"
  fi
  # Read the paths the patch really names while the new files are still visible
  # to `diff`: afterwards they are untracked again and `diff` would not list them.
  want=$(gitq diff --name-only HEAD -- "${sel[@]}" | LC_ALL=C sort | tr '\n' ' ')
  gitq reset -q -- "${newonly[@]}"
else
  gitq diff HEAD --binary -- "${sel[@]}" >"$patch"
  want=$(gitq diff --name-only HEAD -- "${sel[@]}" | LC_ALL=C sort | tr '\n' ' ')
fi
if [ ! -s "$patch" ]; then die "the patch came out empty"; fi
say "  $patch ($(grep -c '^diff --git' "$patch") file(s))"
# Prune old patches: newest $KEEP_PATCHES stay, the rest go. `ls -t` is the only
# ordering available without anything outside core git installed.
if [ "${KEEP_PATCHES:-0}" -gt 0 ] 2>/dev/null; then
  ls -t "$gitdir/publish"/*.diff 2>/dev/null | tail -n +"$((KEEP_PATCHES + 1))" |
    while IFS= read -r old; do rm -f -- "$old"; done
fi

step "$branch and $remote/$branch"
phase=publish
gitq fetch -q "$remote"
if [ "$(gitq rev-parse "$branch")" != "$(gitq rev-parse "$remote/$branch")" ]; then
  if gitq merge-base --is-ancestor "$branch" "$remote/$branch"; then
    say "  $branch trails $remote/$branch: fast-forwarding it"
    gitq fetch -q "$remote" "$branch:$branch"
  else
    die "$branch has commits $remote/$branch does not, and $branch only fast-forwards:
  git -C $repo log --oneline $remote/$branch..$branch
  move them onto $pub and push them (README「提交往哪去」)"
  fi
fi

step "publish on $pub"
gitw fetch -q "$remote"
gitw checkout -qB "$pub" "$remote/$branch"
if ! gitw apply --whitespace=nowarn "$patch"; then
  die "the patch does not apply on $remote/$branch -- the main checkout was not touched.
  the changes are still in the working tree; the patch is $patch

  Two causes, and only the first is a retry:
  1. $branch moved since they were written -- run this again, it fetches.
  2. the change's own context is content only 'local' has: the machine block at
     the end of providers.toml, a file only the machine commit added. A change
     there is a machine change -- commit it on local with a 'local:' message."
fi
gitw add -A -- "${sel[@]}"
# A patch that changes only a file mode applies to nothing here: `git apply`
# drops mode changes when core.fileMode is false, which is how a Windows clone
# is configured. Say so instead of failing at `git commit`.
if gitw diff --cached --quiet; then
  die "the patch applied to no content on $pub -- nothing to commit.
  a mode-only change is the usual cause: this checkout records no file modes
  ('git config core.fileMode' is $(gitq config --get core.fileMode || echo unset)),
  so 'git apply' drops it. the main checkout was not touched."
fi
gitw commit -q -m "$message"
got=$(gitw show --name-only --pretty=format: HEAD | sed '/^[[:space:]]*$/d' | LC_ALL=C sort | tr '\n' ' ')
if [ "$got" != "$want" ]; then
  gitw reset -q --hard "$remote/$branch"
  die "the commit would carry [$got] but these paths changed [$want] -- nothing was pushed"
fi
if ! gitw push -q "$remote" "$pub:$branch"; then
  gitw reset -q --hard "$remote/$branch"
  die "the push was refused -- the main checkout was not touched.
  a non-fast-forward means $remote/$branch moved; run this again"
fi
say "  pushed $(gitw rev-parse --short HEAD) to $remote/$branch"

step "land on local"
phase=land
aside=""
if [ ${#rest[@]} -gt 0 ]; then
  gitq stash push -q -u -m "publish: kept aside" -- "${rest[@]}"
  aside=$(gitq rev-parse -q --verify 'stash@{0}')
  say "  kept aside (not published): ${rest[*]}"
fi
# The published content is about to be in HEAD, so the tree has to lose it first:
# a rebase refuses to run over a dirty tree, and leaving it dirty would make the
# same changes appear twice.
sel_tracked=()
sel_new=()
for p in "${sel[@]}"; do
  if contains "$p" "${newonly[@]}"; then sel_new+=("$p"); else sel_tracked+=("$p"); fi
done
if [ ${#sel_tracked[@]} -gt 0 ]; then gitq checkout -q -- "${sel_tracked[@]}"; fi
if [ ${#sel_new[@]} -gt 0 ]; then rm -f -- "${sel_new[@]}"; fi
gitq fetch -q . "refs/heads/$pub:refs/heads/$branch"
# A conflict here is aborted rather than left standing: the server reads this
# working tree, so a checkout parked mid-rebase is the one state it must never
# be in. The commit is already on the remote, so nothing is lost by undoing it.
if ! gitq rebase -q "$branch"; then
  if [ -e "$gitdir/rebase-merge" ] || [ -e "$gitdir/rebase-apply" ]; then
    gitq rebase --abort >/dev/null 2>&1 || true
  fi
  leave_as_found
fi
if [ -n "$aside" ]; then
  if gitq stash apply -q "$aside"; then
    if [ "$(gitq rev-parse -q --verify 'stash@{0}' || true)" = "$aside" ]; then
      gitq stash drop -q
    fi
    say "  put back: ${rest[*]}"
  else
    say "!! the changes kept aside do not apply on the new $branch -- they are still stashed:"
    say "   git -C $repo stash list    git -C $repo stash show -p $aside"
  fi
fi

phase=check
step "checks"
[ "$(gitq rev-parse --abbrev-ref HEAD)" = local ] ||
  die "the main checkout left local; put it back: git -C $repo checkout local"
if [ "$(gitq rev-parse "$branch")" != "$(gitq rev-parse "$remote/$branch")" ]; then
  die "$branch and $remote/$branch disagree after the push"
fi
if [ -n "$(gitq log --oneline "local..$branch")" ]; then
  die "local does not contain $branch -- the rebase did not land"
fi
stray=$(gitq log --format=%s "$branch"..local | grep -v '^local:' || true)
if [ -n "$stray" ]; then
  say "!! between $branch and local, commits that are not 'local:':"
  printf '   %s\n' "$stray"
fi
if ! gitq apply --check --reverse "$patch"; then
  say "!! HEAD does not carry the patch exactly -- look at it before trusting this"
fi
left=$(gitq status --short)
if [ -n "$left" ]; then
  say "  still uncommitted (not published):"
  printf '   %s\n' "$left"
fi
say "done: $(gitq rev-parse --short local) on $branch, $(gitq rev-parse --short "$remote/$branch") on $remote; local commits: $(gitq rev-list --count "$branch"..local)"
