#!/usr/bin/env bash
# Turn the main checkout's uncommitted changes into a public commit on `main`,
# and rebase `local` onto it, without the main checkout ever leaving `local`.
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
[ -d "$wt" ] || die "no worktree at $wt_rel -- create it once:
  git -C $repo worktree add $wt_rel -B $pub $remote/$branch"

head=$(gitq rev-parse --abbrev-ref HEAD)
[ "$head" = local ] || die "the main checkout is on '$head', not 'local'.
  the server reads this working tree, so it stays on local:
    git -C $repo checkout local"

gitdir=$(cd "$repo" && cd "$(gitq rev-parse --git-dir)" && pwd)
for mark in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
  if [ -e "$gitdir/$mark" ]; then
    die "$mark is in progress in the main checkout; finish or abort it first"
  fi
done

pub_dirty=$(gitw status --porcelain)
if [ -n "$pub_dirty" ]; then
  die "the worktree $wt_rel has uncommitted changes:
$pub_dirty
  it is a workspace: commit or discard them, then run this again"
fi

mapfile -t modified < <(gitq diff --name-only HEAD)
mapfile -t newer < <(gitq ls-files --others --exclude-standard)
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
  say "  commit on $pub at $remote/$branch: \"$message\""
  say "  push it to $remote $branch, fast-forward $branch, rebase local onto it"
  if [ ${#rest[@]} -gt 0 ]; then
    say "  keep aside and put back after the rebase: ${rest[*]}"
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

step "the patch"
mkdir -p "$gitdir/publish"
stamp=$(date +%Y%m%d-%H%M%S)
patch=$gitdir/publish/$stamp.diff
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
