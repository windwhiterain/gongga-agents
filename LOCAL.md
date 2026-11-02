# 这个 checkout 用哪个分支

**`local`** 是这台机器在读的那一个。`main` 是上游。

**主检出始终检出 `local`**：服务端读的是这个目录的工作区，不是分支名。换分支、rebase、解冲突
都在 `.worktrees/work` 里做 —— 切走一次，这台机器当场就在用上游的 roster 跑（见文末「目录」）。

```bash
git -C ~/.agents/gongga-agents branch --show-current    # -> local
```

## 为什么是一条分支，不是一个覆盖层

以前是两份文件：社区 clone 的 `providers.toml`，加上 `~/.agents/providers.toml`
这一层私有覆盖，服务端每次读都按字段合并。那个合并有两件事做不到：

- 它无法回答"现在这个价是哪个价"——覆盖层和 clone 各说一句，谁新谁旧没人看；
- 它让"本机才知道的事"没有自己的历史，改一次就丢一次。

现在本机的东西是 `local` 上的一次提交：内网 `base_url`、自建 provider 都在这一次提交里。
**密钥不在里面** —— 它在 `~/.agents/keys.toml`，`~/.agents` 不是仓库，所以那个文件永远
不会被提交（见 `README.md` 的「密钥政策」）。

## 上游更新：rebase

```bash
cd ~/.agents/gongga-agents/.worktrees/work
git fetch origin
git fetch -q . refs/remotes/origin/main:refs/heads/main   # 上游 --> 本地 main（只快进）
cd ~/.agents/gongga-agents
git rebase main                                          # 本地 main --> local
```

`local` 上只有本机那几个字段（内网 host、自建 provider），所以 rebase 几乎不会冲突 ——
真撞了也是"这台机器的 host"和"上游改同一行"，不是整个文件。撞了就先 `git rebase --abort`
（工作区立刻回到干净的 `local`），按 `README.md` 第三节在 worktree 里把结果算好，
再用一条 `git reset --hard` 落下来。

这条其实也由服务端每小时的同步代劳（`fetch`、快进 `main`、`local` 是检出分支时 rebase 它）。
手动跑一遍只是为了上游刚动、你马上要用时不必等下一个整点。

## 推送

`main` 可以推；**`local` 不推**，也不要 `git push --all`。

```bash
cd ~/.agents/gongga-agents/.worktrees/work
git push origin pub:main
cd ~/.agents/gongga-agents
git fetch -q . refs/heads/pub:refs/heads/main && git rebase main
```

`local` 永远只在本地：它讲的是这台机器的事（内网 host、自建 provider），别人用不上。
公共提交压在 `local` 上时怎么搬回 `main`，写在 `README.md` 的「提交往哪去」（第三节）。

密钥不在这条分支上，所以"推错分支"不再是泄密路径 —— 那是 `keys.toml` 的活儿，
它根本不在仓库里。

## 目录：主检出一直在 `local`，分支工作在 worktree 里

服务端读的是**主检出的工作区**（这个目录里的 `providers.toml` 和 `skills/`），不是分支名。
所以主检出切到 `main`、detach、或停在 rebase 的冲突态，这台机器就在用上游的 roster 跑，
而且不报错。换分支、rebase、解冲突一律在 `.worktrees/work` 里做：

```bash
git -C ~/.agents/gongga-agents branch --show-current    # 永远 local
git -C ~/.agents/gongga-agents worktree list
```

那段 worktree 是一次性的，`pub` 上什么都可以试；合回 `main` 之后，主检出只需一条
`git rebase main`（或第三节末尾那条 `git reset --hard`），全程没有离开 `local`。

