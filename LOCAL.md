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

现在本机的东西是 `local` 上的一次提交：密钥写在 `key_env` 旁边（`key_env` 仍然优先），
想加内网 base_url、自建 provider 也都在这一次提交里。

## 上游更新：rebase

```bash
cd ~/.agents/gongga-agents
git fetch origin
git checkout main && git rebase origin/main     # 上游 --> 本地 main
git checkout local && git rebase main           # 本地 main --> local
```

`local` 上只有本机那几个字段（密钥、可能的私有 host），所以 rebase 几乎不会冲突。
万一冲突，解决的是"这台机器的 key"和"上游改了同一行"，不是整个文件。

## 推送

`main` 可以推（它没有密钥）；**`local` 不推**，也不要 `git push --all`。

```bash
git push origin main
```

`local` 永远只在本地。这是密钥能安全放在磁盘上的原因。
