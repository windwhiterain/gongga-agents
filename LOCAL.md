# 这个 checkout 用哪个分支

**`local`** 是这台机器在读的那一个。`main` 是上游。

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
cd ~/.agents/gongga-agents
git fetch origin
git checkout main && git rebase origin/main     # 上游 --> 本地 main
git checkout local && git rebase main           # 本地 main --> local
```

`local` 上只有本机那几个字段（内网 host、自建 provider），所以 rebase 几乎不会冲突。
万一冲突，解决的是"这台机器的 host"和"上游改了同一行"，不是整个文件。

## 推送

`main` 可以推；**`local` 不推**，也不要 `git push --all`。

```bash
git push origin main
```

`local` 永远只在本地：它讲的是这台机器的事（内网 host、自建 provider），别人用不上。
公共提交压在 `local` 上时怎么搬回 `main`，写在 `README.md` 的「提交往哪去」（第三节）。
密钥不在这条分支上，所以"推错分支"不再是泄密路径 —— 那是 `keys.toml` 的活儿，
它根本不在仓库里。
