# 课堂规范执行简报资料

人、班和学校都是匿名编号。时钟以业务日计。规范用年级和任务类别指定范围，`effective` 是开始日；普通在途任务按首次开放适用，除非明确批准改变。`required` 是需要履行的步骤。指标的最小发布人数按不同学生计，不按任务行数计。跨周汇总时，重复学生在组内只计一次。
`exceptions` 是发文单位给学校的文件行；相同范围的不同认可版本没有缺省优先级，只有撤销或裁定能解除冲突。`approval_day` 是文件写明的时间，`received` 是督导实际收到的时间；取消某文件以 `cancels` 指向原编号。
收件授权区间含起始、不含结束。历史释出行是用户已收到的汇总，这些值来自旧业务过程，不是预期新输出；`cohort` 对应原简报释出覆盖的匿名学生。分母不足四人时，既要判断本格，也要考虑历史释出与新汇总一起构成的可推导量。教师跟进是处理自己工作所需的例外，不是公开汇总发布。

## 资料格式

资料是匿名合成登记，不包含真实学生、真实成绩或外部访问地址。UTF-8 JSON，每张表是对象数组；`null` 表示该字段无已知值。`records/catalog.json` 记录每张表的字段。

- `policies`：改动前后规范。 字段：`policy`、`grade`、`category`、`effective`、`required`。
- `tasks`：重开只延长原登记观察，不产生新任务。 字段：`task`、`school`、`room`、`grade`、`category`、`opened`、`due`、`reopened`、`owner`。
- `roster`：各任务的应参与学生。 字段：`task`、`student`。
- `steps`：happened 为行为发生日；补传为更晚的 received。 字段：`task`、`student`、`step`、`happened`、`received`。
- `exceptions`：影响任务的批准文件及撤销记录。 字段：`document`、`task`、`policy`、`approval_day`、`received`、`cancels`。
- `recipients`：每行是一个授权；空结束表示无到期日。 字段：`recipient`、`role`、`area`、`dimension`、`start`、`end`。
- `released`：实际历史释出记录，不含尚未发布的汇总。 字段：`issue`、`recipient`、`dimension`、`cohort`、`numerator`、`denominator`、`sent`、`indicator`。
- `editions`：简报计划与历史发放记录。 字段：`issue`、`cutoff`、`state`、`recipients`。

## 本地查看

使用 Python 3.11 标准库，无需安装包。用 `python3 record_reader.py` 检查并列出收到的表行数；也可传入同格式资料目录。用 `python3 -m unittest discover -v` 检查资料契约。读取入口不会计算分析结果。
