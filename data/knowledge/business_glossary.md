# 业务口径与指标字典

> 检索到的条目会作为“业务知识”注入 Agent 1 的上下文。写 SQL 前先确认业务词在本文件中的定义。

## 活跃用户

活跃用户指**最近 30 天内有过登录记录**、且未被软删除的用户。

- 判定表：`user_login.login_time`、`users.is_del`
- 口径：`users.is_del = 0 AND user_login.login_time >= date('now', '-30 days')`
- 同一用户多次登录只算一个，统计时用 `COUNT(DISTINCT user_id)`

## 高净值客户

高净值客户指**在我行资产总额达到 100 万元（1,000,000 元）及以上**的客户。

- 判定字段：`users.asset_amount`
- 口径：`users.asset_amount >= 1000000 AND users.is_del = 0`
- “资产 100W”“百万资产客户”均指同一口径

## 有效订单

有效订单指**未取消且未被删除**的订单。

- 判定字段：`orders.order_status`、`orders.is_del`
- 口径：`orders.order_status <> 5 AND orders.is_del = 0`
- 已支付：`order_status IN (2, 3, 4)`

## 华东区

华东区包含以下省份：**上海、江苏、浙江**（本库数据范围内）。

- 关联路径：`dim_region.region_name = '华东'` → `dim_city.province_code` → 城市编码
- 大区与省份的对应关系以 `dim_region` 表为准，不要手写省份列表

## 爆款

爆款指**在统计周期内销量（销售件数）排名靠前的商品**，不依赖任何手工标记字段。

- 判定字段：`sales_record.quantity`
- 口径：按 `product_id` 汇总 `quantity` 降序取前 N 名

## GMV / 销售额

GMV 指统计周期内的销售流水总额。

- 判定字段：`sales_record.sales_amount`
- 口径：`SUM(sales_record.sales_amount)`，已剔除取消与软删除订单

## 客单价

客单价 = 统计周期内有效订单总金额 / 有效订单数。

- 判定字段：`orders.amount`
- 口径：`SUM(amount) / COUNT(DISTINCT order_id)`

## 点击转化率

APP 的点击转化率指**点击次数 / 曝光次数**。

- 判定表：`app_event.event_type`（`expose`=曝光、`click`=点击、`order`=下单）
- 口径：`SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END) * 1.0 / SUM(CASE WHEN event_type = 'expose' THEN 1 ELSE 0 END)`
- 下单转化率同理，把 `click` 换成 `order`

## 流失 / 流失预警

流失客户指**最近 60 天没有登录记录**的用户（仅针对非软删除用户）。

- 判定表：`user_login.login_time`、`users.is_del`
- 口径：`NOT EXISTS (SELECT 1 FROM user_login l WHERE l.user_id = u.user_id AND l.login_time >= date('now', '-60 days'))`

## 软删除

除 `dim_*` 维表外，业务表都有 `is_del` 字段：`0` 表示有效，`1` 表示已删除。任何统计默认只看 `is_del = 0` 的数据。

## 性别编码

`users.gender` 存的是编码，不是文本：**1 = 男，2 = 女**。用户说“男性用户”时要映射为 `gender = 1`。

## 渠道

`sales_record.channel` 取值：`app`（APP）、`h5`（H5 页面）、`pc`（PC 端）。
