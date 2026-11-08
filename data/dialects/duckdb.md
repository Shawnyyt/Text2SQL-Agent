# DuckDB 方言补丁

> 该库为 DuckDB。生成 SQL 时注意与 MySQL / SQLite 的差异。

## 日期时间

| 需求 | 写法 |
|---|---|
| 取日期部分 | `CAST(event_time AS DATE)` 或 `date(event_time)` |
| 日期截断 | `date_trunc('month', event_time)`、`date_trunc('day', event_time)` |
| 当前日期 | `current_date` |
| 日期加减 | `event_time - INTERVAL 1 DAY`、`current_date - INTERVAL '30' DAY` |
| 两个日期相差天数 | `date_diff('day', a, b)` |

## 比率与数值

- **整数相除会截断**：`click_cnt / expose_cnt` 在整数列上会得到 0，必须写成
  `CAST(click_cnt AS DOUBLE) / expose_cnt`，或乘 `1.0`。
- 保留小数用 `ROUND(x, 4)`。
- 条件计数：`COUNT(*) FILTER (WHERE event_type = 'click')`，也可用 `SUM(CASE WHEN ... THEN 1 ELSE 0 END)`。

## 字符串与聚合

- 拼接用 `||`，也支持 `concat()`。
- `NULLIF(x, 0)` 用于避免除以零。
- 支持 `QUALIFY`，但优先用子查询写法保证可移植。

## 分页与限制

- `LIMIT n`、`LIMIT n OFFSET m`。

## 其他注意

- 表名和列名小写，不加引号。
- 分区字段 `dt` 是 `DATE`，按天过滤直接 `dt = DATE '2026-11-07'` 或 `dt = current_date - INTERVAL 1 DAY`。
- 支持窗口函数、CTE、`UNION ALL`。
