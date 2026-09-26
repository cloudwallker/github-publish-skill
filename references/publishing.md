# 发布与身份流程

## 依赖与认证

使用 Git、Python 3.9+、GitHub CLI `gh`、Gitleaks 8.19+。先查 PATH，也可使用已存在且可信的绝对路径。缺失工具时说明具体缺项，使用官方发行渠道；安装是补齐依赖，不能顺带改全局 hook 或其他项目配置。

- GitHub CLI：[安装说明](https://cli.github.com/)，登录使用 `gh auth login`，检查用 `gh auth status`。不要调用 `gh auth token` 或打印凭据文件。
- Gitleaks：[官方发布](https://github.com/gitleaks/gitleaks/releases)，下载适合平台的版本并验证官方校验和，再运行 `version`。不能把未经验证的项目内同名可执行文件当扫描器。
- 不要求用户把 token 粘贴到对话。认证失败时完成可独立准备的工作，再说明需要用户登录。

## 身份核验

1. 从当前目标 GitHub host 的已认证会话读取账户 login/id；私有仓库与 API 必须使用同一身份。用 GitHub CLI 的结构化数据解析，不整段回显原始配置或邮箱列表。
2. 核对 Git 的有效 `user.name`、`user.email` 和 `GIT_AUTHOR_*` / `GIT_COMMITTER_*` 环境覆盖。用户名只是署名文本，不能代替邮箱关联验证。
3. noreply 必须有可靠的账户关联证据：用户 GitHub 邮箱设置显示的地址，或 GitHub 对该地址的既有提交归属与当前账户一致。不要仅凭 `login/id` 拼接地址后称“已验证”。API 提供已验证邮箱时可以确认关联，但真实私人邮箱不得未经授权公开。
4. 信息不足时询问用户确认自己邮箱设置中的 noreply 地址。保存偏好时只保存必要的公开署名/noreply，不保存 token、真实私人邮箱列表或设置截图。
5. 仅在当前发布仓库设置 `git config --local user.name ...` / `user.email ...`，将本次新提交 author/committer 绑定到已核实身份；检查环境覆盖是否导致不同身份。
6. 提交后再次核对实际 commit 元数据；远程发布后核对 GitHub 返回的 author/committer 账户关联。无关联时报告未完成身份验证，不冒充归属成功。

用户新提交不得署名 Codex、OpenAI、Claude、AI Assistant 或自动加入 AI `Co-authored-by`。第三方原有历史、许可证和版权署名保留；干净首发保留必要的来源说明。

参考：[GitHub 邮箱与提交归属](https://docs.github.com/en/account-and-profile/concepts/email-addresses)。

## 新仓库：干净首发

1. 确认用户授权的项目路径及仓库 owner/name，默认私有。检查是否已有目标仓库，已有则转入更新模式，不能覆盖。
2. 在源项目之外创建命名明确的独立发布目录；从保留清单复制成品，不复制 `.git`、AI 过程资料或个人配置。源项目不初始化新 remote、不重置历史、不删除文件。
3. 按 [源码组织规范](source-layout.md) 轻量整理发布目录，完成 README、图片和示例配置，做项目适用的运行/构建验证及 `files` 检查。安装包等 Release 候选产物另列清单，不混入源码提交。
4. 仅在发布目录 `git init -b main`，设置已核实的本地作者身份；显式暂存清单，运行 `staged` 检查，再提交。
5. 使用 `commits --base EMPTY --head <确定SHA>` 检查完整首发历史，清单与检查结论一致后才创建仓库。
6. 创建仓库时显式传 `--private`（明确授权公开才用 `--public`）和真实中英 description。不要用 `gh repo create --push` 跳过提交后的检查。
7. 将明确 SHA 推送到明确分支，例如 `git -c push.followTags=false push origin <检查过的SHA>:refs/heads/main`，不批量推标签。验证远程结果。

## 已有仓库：延续历史

1. 核对 origin、owner/name、目标分支和可见性；不要打印带凭据的 remote URL。读取远程状态并 fetch 正确分支，记录远程 tip SHA。
2. 要求完整历史，浅克隆先补齐。检查本地与远程是否分歧；发生分歧时先协调，不能通过自动 pull、rebase 或 force 推送掩盖。
3. 保护已有未提交改动和他人的暂存文件，显式调整本次清单；若暂存区混有无关改动，使用独立工作区或整理到可明确区分的提交，不能直接 `git add -A`。
4. 沿用现有布局，只作必要的 [轻量整理](source-layout.md)，同步引用并验证。完成 files、staged 检查和作者验证，创建本次提交。以已记录的远程 tip 为 `--base`，固定本地 SHA 为 `--head` 检查待推历史；新远程分支用 EMPTY 检查整个可达历史。
5. 推送前重新检查相同输入的 fingerprint，再次读取远程分支；远程变化时重新协调和审查。用固定 SHA 显式推送，不推检查后新增的提交。
6. 仅用户要求 PR、或仓库保护分支必须通过 PR 时走分支/PR。已有 PR 复用且保留 draft 状态；新 PR 写清实际改动，body 用 UTF-8 文件传入以保留换行。

Git LFS、子模块、链接、超大或特殊二进制如果无法完整审查，其上传不得被普通文本扫描结果授权。检查引用的真实对象及任何附带上传动作后再制定对应发布方案。

## 发布后的验证

读取远程目标分支 SHA，核对等于已扫描 SHA；检查远程最终树与发布清单一致，无意外过程文件。读取仓库可见性、description、README 与图片路径；查看渲染效果，而非只确认 CLI 成功退出。

更新简介时保留有价值的现有内容和链接。双语 description **英文在前**，默认写成 `One concrete English value proposition. | 对应的中文简介`：说清项目类型与核心用途，避免堆技术栈、夸大能力或直接复制 README 长副标题。本 Skill 控制为不超过 300 个 Unicode 字符，优先压缩冗词并保留中英核心意思；服务器拒绝长度时进一步压缩后核验。

README 默认采用英文 `README.md` 与中文 `README_ZH.md`，首页显示 `English | 中文` 语言入口；已有中文文件名则沿用，两个文件互相链接。正文事实、安装步骤和图片版本保持一致。首屏写法见 [展示规范](presentation.md)。

## 源码与 Release 的分工

上述流程上传源码及必要资源，保留可构建、可使用的仓库。GitHub Release 是绑定版本标签的发行页，版本说明和安装包等附件按 [Release 流程](releases.md) 操作；GitHub 自动生成的 Source code 下载来自标签快照，不等于已构建的安装包。

只有用户明确要求发布版本、Release 或下载包时才继续版本发布，不把普通“上传项目”扩展成 Release。Release 沿用仓库可见性；私有仓库的 Release 不因“发布”一词自动公开。设置 Pages、变更许可证或公开仓库也必须属于用户授权目标。发布失败仅重试可确定幂等的步骤，先读取已有远程状态以避免重复仓库、PR 或 Release。

参考：[gh repo create](https://cli.github.com/manual/gh_repo_create)、[OpenAI yeet](https://github.com/openai/skills/blob/main/skills/.curated/yeet/SKILL.md)、[MengTo 项目发布 Skill](https://github.com/MengTo/Skills/blob/main/agent-skills/codex/publish-project-to-github/SKILL.md)。本 Skill 保留当前用户偏好，不直接照搬参考的 stage-all、默认公开或额外部署行为。
