# trace-replay

**离线、确定性、fail-closed 的 agent 轨迹重放评测器。零第三方依赖，只用 Python 3.10+ 标准库。**

它是「可复现 LLM 评测闭环」里缺失的中间一环：

```
eval-sandbox（怎么安全地跑） → trace-replay（同一份轨迹能不能算出同一个分） → eval-gate（变没变差） → rubric-support-qa（什么算好）
```

一句话：**拿到一段已记录的 agent 轨迹（tool call 序列 + 观测 + 最终答案），不联网、不调模型，把分重算一遍，并证明两次重放字节级相同。**

---

## 3 分钟跑通

```bash
git clone <repo-url> && cd trace-replay
python3 -m trace_replay run --trace examples/traces/ok_search_calc.jsonl --out out
```

预期输出（实测值，见下文「实测数据」）：

```
verdict=PASS checks=4/4 sha256=50aa7357572bbc0ca3414358841615e25c4ad803796fd4591c294e354b94d193 report=out/report.json meta=out/meta.json
```

证明两次重放完全一致：

```bash
python3 -m trace_replay verify --trace examples/traces/ok_search_calc.jsonl --runs 3
# runs=3 sha256=50aa7357572bbc0ca3414358841615e25c4ad803796fd4591c294e354b94d193 identical=true
```

只看录制观测、不跑本地 stub：

```bash
python3 -m trace_replay run --trace examples/traces/ok_search_calc.jsonl --out out --no-stub
```

退出码：`0` PASS / `1` 判决 FAIL / `2` 用法或文件错误 / `3` `verify` 发现多次重放不一致。

---

## 轨迹格式（JSONL，一行一条，顺序固定）

| 行类型 | 位置 | 必填字段 |
| --- | --- | --- |
| `trace` | 必须是第 1 行，只能有 1 个 | `schema_version`(必须=1)、`trace_id`、`task`、`scorers`；可选 `limits` |
| `step` | 中间任意多行 | `index`(必须等于前面 step 的个数)、`span_id`(全局唯一)、`tool`、`args`、`observation`、`latency_ms` |
| `final` | 必须是最后一行 | `index`(必须等于 step 总数)、`answer` |

`observation` 是**录制下来的工具返回**，回放时直接复用，绝不发起真实调用。
`limits` 缺省为 `{"max_steps": 32, "max_repeats": 3}`（代码常量，不是猜的）。

```jsonc
{"type":"trace","schema_version":1,"trace_id":"ok-001","task":"查人口","limits":{"max_steps":8,"max_repeats":3},
 "scorers":[{"id":"final_text","type":"exact_match","config":{"path":"text","expected":"2040"}}]}
{"type":"step","index":0,"span_id":"s0","tool":"search","args":{"query":"population 2040"},
 "observation":{"status":"ok","result":"2040"},"latency_ms":12}
{"type":"final","index":1,"answer":{"text":"2040"}}
```

评分器的 `path` 一律相对 `final.answer` 取值，支持点路径与列表下标（如 `rows.0.amount`）；取不到不做任何默认值兜底，直接判该条 FAIL。

---

## 两种回放模式

| 模式 | 行为 | 用途 |
| --- | --- | --- |
| recorded（默认关闭 stub 时） | 只校验观测 schema，直接复用录制观测 | 纯离线复算分数 |
| stub（默认） | 额外用注册的本地纯函数 stub 重算一次，逐字段比对录制观测 | 发现工具实现漂移（同一轨迹换代码版本后结果变了） |

stub 比对只比较 **stub 真正产出的字段**，录制端可额外附带 `status` 等元数据；数值按数值相等（`5 == 5.0`），bool 与数字严格区分。
未注册的工具直接 `E_TRACE_UNKNOWN_TOOL`，不会退化成"放行"。

内置工具（`trace_replay/tools.py`，可自行 `ToolRegistry.register` 扩展）：
`search`（本地小语料查表）、`calc`（受限 AST 求值，支持 `+ - * / % **`）、`lookup_table`（内存表）、`echo`。

---

## 内置评分器

| type | 配置 | 说明 |
| --- | --- | --- |
| `exact_match` | `path`, `expected`(str), `normalize`(bool, 默认 false) | 字符串精确匹配 |
| `numeric_tolerance` | `path`, `expected`(number), `abs_tol`, `rel_tol` | 容差 = `max(abs_tol, rel_tol*|expected|)` |
| `structural_fields` | `path`, `fields`(name→类型名), `allow_extra`(默认 true) | 字段存在性 + 类型比对；类型名取自 `str/int/number/bool/list/dict` |
| `regex_rubric` | `rules`(数组，每条 `id`/`path`/`pattern`/`must_match`) | 正则断言，全部规则通过才算通过 |
| `tool_sequence` | `expected`(工具名数组) | 工具调用序列必须完全一致 |

新增评分器只需实现 `check(ctx) -> Check` 并注册进 `SCORERS`；每条结果都带稳定 `reason` 码（`ok` / `mismatch` / `missing_path` / `out_of_tolerance` / `rule_failed` …）和结构化 `detail`，便于逐条复核。

---

## 判据清单（这个仓库的验收口径）

1. 同一输入重放 N 次，`report.json` 字节级相同，`sha256` 相同（`verify` 命令与测试共同锁定）。
2. 报告里不出现时间戳 / 耗时 / 主机名 / 绝对路径；易变信息只写 `meta.json`。
3. 工具调用不联网：静态 AST 扫描 + 运行期 socket 封禁双重测试。
4. 运行时零第三方依赖：静态 AST 扫描导入模块集合。
5. 字段缺失 / 类型不符 / 超步数 / 循环调用 / 工具不存在 / 观测 schema 不符 / stub 漂移 → 全部 FAIL 且带稳定错误码，不静默跳过。
6. 每个错误码都有可运行用例；失效模式表 ≥10 条（实际 17 条）。
7. 评分器逐条判据有单测（含反例）。

---

## 错误码

`python3 -m trace_replay list-errors` 会打印同一份清单（与 `trace_replay/errors.py` 的 `ERROR_CODES` 共用，避免文档漂移）。

| 分组 | 错误码 |
| --- | --- |
| 文件/结构 | `E_TRACE_IO` `E_TRACE_ENCODING` `E_TRACE_EMPTY` `E_TRACE_EMPTY_LINE` `E_TRACE_BAD_JSON` `E_TRACE_BAD_TYPE` `E_TRACE_MISSING_FIELD` `E_TRACE_EMPTY_FIELD` `E_TRACE_UNKNOWN_RECORD` `E_TRACE_SCHEMA_VERSION` `E_TRACE_META_MISSING` `E_TRACE_META_DUP` `E_TRACE_DUP_SPAN` `E_TRACE_DUP_SCORER` `E_TRACE_INDEX` `E_TRACE_FINAL_MISSING` `E_TRACE_FINAL_DUP` `E_TRACE_FINAL_NOT_LAST` |
| 回放 | `E_TRACE_UNKNOWN_TOOL` `E_TRACE_TOOL_ARGS` `E_TRACE_OBS_SCHEMA` `E_TRACE_STEP_LIMIT` `E_TRACE_LOOP` `E_TRACE_SCHEMA_UNKNOWN_TYPE` `E_TOOL_STUB_ERROR` `E_TOOL_REPLAY_DIVERGENCE` |
| 评分 | `E_SCORER_UNKNOWN` `E_SCORER_CONFIG` `E_SCORER_INTERNAL` |

`E_TRACE_LOOP` 覆盖周期 1/2/3 的连续重复；判定阈值是 `max_repeats + 1` 次完整重复。

---

## 确定性是怎么被证明的

- `report.json` 由 `json.dumps(sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)` 生成，唯一且可预测。
- 报告里只有输入决定的字段：`trace_id`、`task`、轨迹文件 `sha256`、判决、逐条 check 与计数。
- 时间戳、耗时、Python 版本、报告哈希写在 `meta.json`，与 `report.json` 分离。
- `report_sha256` 就是对实际写出的 `report.json` 字节求哈希，可直接 `sha256sum` 复核。
- `verify` 子命令内部重复跑完整「解析 → 回放 → 评分」并比对哈希，不是"声称"，是每条 CI 都会执行的一步。

---

## 实测数据（2026-11-04，Ubuntu 24.04 / Python 3.12.3 / 本地 CPU）

| 项目 | 结果 |
| --- | --- |
| `pytest` | **111 passed**（含 17 条 parametrize 失效模式） |
| `mypy --strict trace_replay` | **Success: no issues found in 10 source files** |
| `verify --runs 3` | `sha256=50aa7357572bbc0ca3414358841615e25c4ad803796fd4591c294e354b94d193 identical=true` |
| 两次 `run` 到不同目录 | `report.json` 两个 `sha256sum` 完全相同；`meta.json` 仅时间戳/耗时不同 |
| 示例轨迹 7 条 | 2 条 PASS，5 条按预期 FAIL（loop / unknown tool / obs schema / stub 漂移 / 截断） |
| 运行时依赖 | 0（标准库之外无安装项） |

> CI 的 3.10–3.12 矩阵配置已写入 `.github/workflows/ci.yml`，但本机只有 3.12 解释器，**尚未在 GitHub Actions 上真实跑过**，见「已知局限」。

---

## 这个仓库能证明什么 / 不能证明什么

**能证明：**

- 同一份轨迹文件在同一台机器上重复重放，`report.json` 字节级一致（哈希可当场复核）。
- 轨迹损坏、工具不存在、观测 schema 不符、循环调用、stub 漂移等情况会 fail-closed 并给出稳定错误码，不会静默给分。
- 工具调用不联网、运行时不引入第三方包（有静态与运行期测试）。
- 五类评分器在正例和反例上的行为是确定的、被测试锁住的。

**不能证明（也不声称）：**

- **不能证明跨机器 / 跨 Python 小版本字节级一致。** 本仓库只在 3.12 实测过；3.10/3.11 的浮点 `repr` 与 dict 行为理论上一致，但未实测。
- **不能证明评分本身正确。** 评分器只是机械执行配置；"什么算好"属于 rubric-support-qa 的职责，本仓库不提供 rubric 有效性证据。
- **不能证明轨迹录制忠实。** 它假定 `observation` 是当时真实返回；stub 模式只能发现"实现漂移"，发现不了"录制时就没录对"。
- **不是 agent 运行时、不是沙箱、不是 gate。** 它不执行真实工具、不做策略阻断、不做版本对比。
- 没有工件签名、没有篡改防护（有人改了轨迹文件，`trace_sha256` 会变，但仓库不验证签名）。
- 不是生产可用组件：无并发、无重试、无持久化调度，单文件单轨迹。

---

## 已知局限

- 循环检测只覆盖**同签名、周期 ≤3 的连续重复**；跨度更长的循环或语义等价但参数不同的循环检测不到。
- stub 比对只覆盖 stub 产出的字段，`status` 等录制端附带的元数据不参与一致性判定。
- 大文件一次性读入内存，无流式解析。
- `latency_ms` 是录制值，只做 `>=0` 校验，不参与评分。
- 错误码集合一旦发布即视为稳定契约，新增可以，重命名算破坏性变更。

---

## 目录结构

```
trace-replay/
├── trace_replay/
│   ├── errors.py     # 稳定错误码与 TraceError
│   ├── model.py      # JSONL 解析、字段校验、规范化 JSON
│   ├── tools.py      # 工具注册表 + 确定性 stub
│   ├── replay.py     # 回放、循环检测、stub 一致性比对
│   ├── scorers.py    # 5 个可插拔评分器
│   ├── engine.py     # 解析→回放→评分→报告（纯函数）
│   ├── report.py     # 报告序列化 / 哈希 / 落盘
│   └── cli.py        # run / verify / list-errors / list-scorers
├── examples/traces/  # 2 条 PASS + 5 条 fail-closed 示例
├── tests/            # 111 个用例
└── .github/workflows/ci.yml
```

## 开发与测试

```bash
python3 -m pip install -e ".[dev]"   # 只有 pytest / mypy，运行期仍零依赖
python3 -m pytest -q
python3 -m mypy --strict trace_replay
```

本地仓库路径：`/home/liuzewen/trace-replay`（仅本地 `git init` + 首个 commit，未 push）。

## 远端地址（待用户决定）

本仓库**没有**创建任何 GitHub 仓库。要发布时需要用户指定并授权：

- 目标地址：`https://github.com/<你的账号>/trace-replay`
- 需要动作：创建远端仓库 → `git remote add origin <url>` → 首次 `git push -u origin main`
- 无历史改写、无 force push、无 PyPI 发布。
