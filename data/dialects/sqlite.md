# SQLite 方言补丁

> 该库为 SQLite 3.x。生成 SQL 时必须遵守以下语法约束，否则执行会报错。

## 日期时间

| 需求 | 写法 |
|---|---|
| 取日期部分 | `date(create_time)` |
| 取年月 | `strftime('%Y-%m', create_time)` |
| 当前日期 | `date('now')` |
| 日期加减 | `date(create_time, '-1 month')`、`date('now', '-30 days')` |
| 两个日期相差天数 | `julianday(a) - julianday(b)` |

不要把日期函数写成 `DATE_FORMAT`、`EXTRACT`、`TO_CHAR`、`DATE_TRUNC`，SQLite 不支持。

## 字符串与聚合

- 拼接用 `||`，没有 `CONCAT()`。
- `IFNULL(x, 0)`、`COALESCE(x, 0)` 都可用。
- 条件求和：`SUM(CASE WHEN ... THEN 1 ELSE 0 END)`。
- 去重用 `COUNT(DISTINCT col)`。

## 分页与限制

- `LIMIT 3`、`LIMIT 10 OFFSET 20`。
- 没有 `TOP n`、没有 `ROWNUM`。

## 其他注意

- 金额是 `REAL`，展示时用 `ROUND(SUM(amount), 2)`。
- 布尔值用 `0/1`。
- 不支持 `ILIKE`，模糊匹配用 `LIKE '%关键词%'`（SQLite 的 LIKE 对 ASCII 不区分大小写）。
- 支持 `WITH` 公共表表达式和窗口函数。
- 不支持 `FULL OUTER JOIN`（本库没有此需求，用 `LEFT JOIN` 即可）。
- 一条 SQL 只能有一个语句，语句末尾分号可有可无。
