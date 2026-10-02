# ec-lab

小创意开源实验室。把自己做出来、确实在用的 AI 技能整理出来开源。

这里的每个技能都是先解决自己的问题，再拿出来给别人用。所以它们目标单一、边界清楚，不做大而全。

## 技能清单

| 技能 | 一句话 | 版本 |
| --- | --- | --- |
| [ec-external-view-filter](skills/ec-external-view-filter) | 对外交付前，把只该内部看的内容从文档和页面里清掉 | v1.9 |

清单陆续补。

## 怎么用

每个技能一个目录，目录里的 `SKILL.md` 是技能本体。

把技能目录整个放进你 AI 助手的技能目录，剩下的事它会按 `SKILL.md` 自己办。除了 `SKILL.md`，目录里其余文件都是可选资源，缺了也能跑。

某个技能到底解决什么问题、怎么触发、边界在哪，看它自己的 `README.md`。

## 仓库结构

```
skills/_template/       新建技能时复制这份
skills/<skill-name>/    每个技能一个目录
```

一个技能目录的标准配置：

```
SKILL.md      技能本体，必填
README.md     给人看的说明，必填
CHANGELOG.md  版本变更记录，必填
icon.png      技能图标，必填
examples/     示例（可选）
references/   参考材料（可选）
scripts/      脚本（可选）
```

## 许可

MIT，见 [LICENSE](LICENSE)。

欢迎提 Issue 和 PR，规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。
