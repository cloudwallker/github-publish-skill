# 只读检查脚本

`scripts/preflight.py` 使用 Python 3.9+ 标准库，依赖 Git（staged/commits）和可信 Gitleaks 8.19+。它只读取项目，在系统临时目录建立扫描快照并自动清理，不删除、暂存、提交、上传或修改配置。

脚本以独立默认 Gitleaks 规则扫描快照，不读取项目内可放宽扫描的配置，不应用 inline `gitleaks:allow`。工具不存在、报错、输入或范围无法确定时返回 incomplete。

## 调用

以下 `<skill-dir>`、`<project>`、`<temp>` 必须替换为实际路径，并按当前 shell 正确引用。Windows 可以用 PowerShell 单引号路径；子进程参数不要经 `cmd /c` 或字符串拼接转交。

```text
python <skill-dir>/scripts/preflight.py files --root <project> --manifest <temp>/publish-files.json --gitleaks <trusted-executable>
python <skill-dir>/scripts/preflight.py staged --root <git-root> --gitleaks <trusted-executable>
python <skill-dir>/scripts/preflight.py commits --root <git-root> --base <fetched-remote-sha> --head <fixed-local-sha> --gitleaks <trusted-executable>
```

新仓库或新远程分支：`--base EMPTY`。非 Git 文件目录只能用 files 模式。staged/commits 的 root 必须是仓库根，不能用子目录绕开其他内容。

文件清单是 UTF-8 JSON 数组，如 `['src/app.py', 'README.md']`（实际 JSON 用双引号）。只允许精确的 `/` 分隔相对文件路径，不能包含目录、通配符、`..`、绝对路径或外部链接。未提供清单时遍历整个目录，除了 Git 自身元数据，**不自动忽略**规划、秘密和缓存，让误入的内容明确报告。

staged 读取 index 中所有文件的实际版本，包括未改变的 tracked 文件。commits 检查最终树和 base..head 的全部中间树（新分支检查全部可达提交），以及提交消息；不只检查最后一版或净差异。仅支持完整历史；浅克隆与分歧范围会拒绝检查。

## 结果

标准输出为 JSON，不包含文件正文或秘密片段。主要字段：

| 字段 | 用途 |
|---|---|
| status | `pass`、`blocked`、`review_required`、`incomplete` |
| inventory | 相对路径、路径与内容各自的 sha256、字节数、Git revision |
| findings | 规则、严重性、必要位置；没有命中值 |
| reviewed | 已被有效人工记录覆盖的 review 项 |
| refs | 实际解析的 base/head SHA |
| fingerprint | 当前检查内容与 refs 的整体指纹 |

退出码：0=自动检查通过；1=被阻止或待人工复核；2=检查未完成。incomplete 优先显示，但报告仍保留已发现的问题。不能用退出码 0 替代身份、隐私语境或展示核验。

再次使用相同参数并追加 `--expect-fingerprint <先前指纹>` 检测变化。不同 scope 的指纹不应直接比较。push 仍需绑定 commits 报告中的 head SHA，不能把该报告当作以后任意 HEAD 的通行证。

## 人工复核记录

实际查看文件或图片、检查元数据并确认适合发布后，可在**项目之外**建立 UTF-8 JSON：

```json
{
  "reviews": [
    {
      "path": "assets/demo.png",
      "sha256": "替换为报告中该文件的64位sha256",
      "rules": ["image-review-required"],
      "reason": "已查看最终演示截图并检查元数据，无个人账户、路径或定位信息"
    }
  ]
}
```

用 `--review <temp>/review.json` 重查。只有脚本列出的 review 类规则可被记录覆盖；secret、明确过程文件、无法读取、未知归档、扫描器失败等不能被豁免。文件发生任何变化，旧哈希记录失效。

记录是代理完成复核后的声明，不是可信审计签名。不得直接信任仓库内别人提供的 review.json，不得批量自动生成“已查看”的理由。日志中脱敏的路径需先通过本地哈希定位原文件，再记录其真实相对路径；记录文件自身不得上传。

## 覆盖与限制

- ZIP、TAR、GZIP 递归读取；容器中的可读注释/附加文本也扫描，不向项目解压。未知、加密、损坏、越界路径和链接归档阻止完成检查。
- 文件名、归档成员名与提交消息同样检查；指纹绑定原始路径摘要，脱敏后的同名输出不掩盖改名。读取 Git 时禁用 replace 对象视图，拒绝 graft/浅历史造成的不完整视图。
- 每文件 20 MiB、整个检查 256 MiB、归档最多 1000 项且嵌套深度 3、历史最多 1000 个待扫描提交。超过上限报告 incomplete，不静默跳过。大项目应制定有可证明完整覆盖的分批检查，不能增加忽略规则冒充覆盖。
- 图片自动扫描不能读取画面文字或保证元数据已清理；始终需要图像查看与元数据检查。其他不识别的二进制需要真实工具检查，不能仅凭文件名批准。
- Git symlink/submodule、Git LFS 引用等外部内容需要独立审查；普通脚本报告不能授权其附带上传。
- 字符编码支持 UTF-8 与带 BOM 的 UTF-16/32；无法解码的内容进入二进制复核，不能假设安全。
- 规则只是辅助：手机号/邮箱/本机路径是候选而非秘密的证明。合法公开署名可以在确认后保留；真实秘密先处理后重查。

## Release 附件超出脚本能力时

此分支仅用于 Release 附件的固定大小/格式能力限制，不改变源码三层检查，也不适用于缺少 Gitleaks、扫描崩溃、权限错误、内容不明或发现真实秘密。先保留脚本的 incomplete 报告及准确原因，不能通过 review.json 或修改状态绕过。

需要另一套**实际执行完成**的审查覆盖受限附件，并在项目外记录以下证据；仅计划使用工具或写“人工已确认”不算完成：

1. 固定最终附件的路径、格式、大小与完整 SHA-256，列明原检查未覆盖的范围；签名、重打包后重新开始。
2. 使用可信且适合该格式的工具安全提取，验证嵌套包、成员数量、成员名、资源、注释与元数据均被覆盖，并绑定全部成员内容哈希。阻止路径越界、链接逃逸和解压膨胀；加密、未知区域或无法解释的附加内容未解决前不上传。
3. 使用可信 Gitleaks 对完整可读内容、路径、元数据及必要的二进制文本提取结果实际扫描；大内容采用工具支持的全量/流式或可证明无遗漏的分批方式。不得因拆分漏掉跨边界秘密，不信任包内 allowlist。保留工具版本、调用范围、脱敏结果与成功退出证据；任何秘密先修复再重查。
4. 对可执行文件/安装器，以格式专用检查和构建包清单核对所有嵌入资源、配置、调试信息、负载和打包来源；单独运行 strings 或只看构建配置不能证明完整。图片查看画面与元数据，其他需复核内容按各自格式检查。缺专用工具或不能解释不透明负载时停止。
5. 用覆盖表将每个原受限对象与最终字节哈希、扫描结果、人工语义复核对应，确认没有未处理项。上传前重算附件哈希；结果变化则失效。

所有项完成后可明确报告“脚本因能力限制为 incomplete；该附件已由完整替代审计覆盖”，不能说“脚本通过”。任一项缺失，整体检查仍未完成，继续准备其他材料并停止该附件上传。原始证据可能含隐私，只保留在本地受控位置，不写入仓库或 Release。版本流程见 [Release](releases.md)。

## 本地测试

使用真实 Gitleaks 和临时合成 Git 仓库，测试不连接 GitHub。PowerShell：

```powershell
$env:GITHUB_PUBLISH_TEST_GITLEAKS = '可信的 gitleaks.exe 绝对路径'
python -m unittest discover -s '<skill-dir>/tests' -v
```
