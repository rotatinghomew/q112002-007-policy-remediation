# 课堂规范执行简报资料

人、班和学校都是匿名编号。时钟以业务日计。规范用年级和任务类别指定范围，`effective` 是开始日；普通在途任务按首次开放适用，除非明确批准改变。`required` 是需要履行的步骤。任务在重开日后有与首次开放同样长的观察期。执行率的统计单位是学生和原任务的配对。
`exceptions` 是发文单位给学校的文件行；相同范围的不同认可版本没有缺省优先级，只有撤销或裁定能解除冲突。`approval_day` 是文件写明的时间，`received` 是督导实际收到的时间；取消某文件以 `cancels` 指向原编号。
`makeup_options` 记录允许认领的原任务与补办窗口，日期含首尾；`work` 是补做活动发生及提示的时间。`claims` 是补救认领，不是完成凭证。裁定可以追溯到两条互相竞争的认领。`owners` 是后到的任课变动；当日开始生效。`deliveries` 是已有的名单发送及认领回执；发送后责任变化应另行通知。简报或名单送达不使用真实邮箱地址。

## 资料格式

资料是匿名合成登记，不包含真实学生、真实成绩或外部访问地址。UTF-8 JSON，每张表是对象数组；`null` 表示该字段无已知值。`records/catalog.json` 记录每张表的字段。

- `policies`：改动前后规范。 字段：`policy`、`grade`、`category`、`effective`、`required`。
- `tasks`：重开只延长原登记观察，不产生新任务。 字段：`task`、`school`、`room`、`grade`、`category`、`opened`、`due`、`reopened`、`owner`。
- `roster`：各任务的应参与学生。 字段：`task`、`student`。
- `steps`：happened 为行为发生日；补传为更晚的 received。 字段：`task`、`student`、`step`、`happened`、`received`。
- `exceptions`：影响任务的批准文件及撤销记录。 字段：`document`、`task`、`policy`、`approval_day`、`received`、`cancels`。
- `owners`：后到的任课变动。 字段：`task`、`owner`、`effective`、`received`。
- `makeup_options`：可用补救练习和补办窗口。 字段：`task`、`exercise`、`start`、`end`。
- `work`：补救登记；hint 为空表示监考确认未提供提示。 字段：`work`、`exercise`、`student`、`independent`、`hint`。
- `claims`：教师提出的认领。 字段：`claim`、`task`、`work`、`teacher`、`day`。
- `rulings`：认领冲突由督导裁定。 字段：`work`、`granted`、`day`。
- `deliveries`：过去通知的发出和认领。 字段：`issue`、`task`、`student`、`teacher`、`sent`、`acknowledged`。
- `editions`：简报排定日和是否发出。 字段：`issue`、`prepared`、`send_day`、`state`。

## 本地查看

使用 Python 3.11 标准库，无需安装包。用 `python3 record_reader.py` 检查并列出收到的表行数；也可传入同格式资料目录。用 `python3 -m unittest discover -s tests -v` 检查资料契约。读取入口不会计算分析结果。

## 简报引擎

`briefing/` 包按业务日回溯计算规范适用、执行率、补救认领与周一分发，口径见 `docs/spec.md`。
读取不受信任资料目录时用 `-I` 并以参数传入路径，例如：

- `python3 -I generate_briefings.py records out`：生成 `out/briefing_packets.json`（各期督导包、教师名单、通知、两期差异）与 `out/summary.md`（中文摘要）。
- `python3 -I -m unittest discover -s tests -v`：资料契约 + 23 项引擎口径测试。
- 引擎只写本地文件，不连接真实邮箱；`recipients.json`、`released.json` 是 catalog 之外的旁路表（路由与历史留痕），缺失时自动降级为空表。
