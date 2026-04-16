/**
 * 离线演示：返回与 FastAPI /api/v1/query 相同结构的模拟数据（不发起网络请求）。
 */
export function fetchDemoQuery(query, targetDb) {
  const delay = 900 + Math.floor(Math.random() * 500)
  const q = (query || '').toLowerCase()

  return new Promise((resolve) => {
    setTimeout(() => {
      let answer
      let sql

      if (q.includes('类别') || q.includes('销售') || q.includes('总额')) {
        sql = `SELECT c.category_name,
       SUM(oi.quantity * oi.unit_price) AS total_amount
FROM order_items oi
JOIN products p ON p.id = oi.product_id
JOIN categories c ON c.id = p.category_id
GROUP BY c.id, c.category_name
ORDER BY total_amount DESC;`
        answer =
          '【离线演示 · 模拟数据】按商品类别聚合的销售总额（示例）：\n\n' +
          '1. 电子产品 · ¥128,400\n' +
          '2. 家居用品 · ¥86,200\n' +
          '3. 服装鞋帽 · ¥54,900\n\n' +
          '以上为前端内置样例，未连接真实数据库与 LLM。'
      } else if (q.includes('城市') || q.includes('分布') || q.includes('电子')) {
        sql = `SELECT u.city, COUNT(DISTINCT u.id) AS buyer_count
FROM users u
JOIN orders o ON o.user_id = u.id
JOIN order_items oi ON oi.order_id = o.id
JOIN products p ON p.id = oi.product_id
JOIN categories c ON c.id = p.category_id
WHERE c.category_name LIKE '%电子%'
GROUP BY u.city
ORDER BY buyer_count DESC;`
        answer =
          '【离线演示 · 模拟数据】购买过「电子产品」相关类目的用户城市分布（示例）：\n\n' +
          '· 深圳：128 人\n' +
          '· 上海：96 人\n' +
          '· 北京：84 人\n' +
          '· 广州：71 人\n\n' +
          '关闭「离线演示模式」并启动后端后，可查询真实库。'
      } else if (q.includes('跨库') || q.includes('行为') || q.includes('转化') || q.includes('加购')) {
        sql = `-- [离线演示 - 多源联邦引擎激活]
-- 挂载库: mysql_business.db, mongo_logs.db
SELECT 
    mysql_db.users.user_id,
    mysql_db.users.member_level,
    COUNT(mongo_db.user_behaviors.behavior_id) as active_actions,
    SUM(mysql_db.orders.total_amount) as total_contribution
FROM mysql_db.users
JOIN mongo_db.user_behaviors 
    ON mysql_db.users.user_id = mongo_db.user_behaviors.user_id
JOIN mysql_db.orders 
    ON mysql_db.users.user_id = mysql_db.orders.user_id
WHERE mongo_db.user_behaviors.behavior_type IN ('加购', '收藏')
GROUP BY mysql_db.users.user_id
HAVING active_actions > 50
ORDER BY total_contribution DESC
LIMIT 5;`
        answer = '### 🌐 跨库追踪：高活跃行为与商业转化 (离线演示模式)\n\n' +
          '引擎已成功启动**跨库路由**，联合了 `mysql_db`(核心交易库) 与 `mongo_db`(行为日志库) 进行深度归因分析。\n\n' +
          '我们提取了频繁产生“加购/收藏”行为（>50次）的高意向用户群体。数据表明，这类“高频互动用户”虽然在业务库中只占 **8%** 的人口比例，但却贡献了高达 **32%** 的总销售额。\n\n' +
          '**前置归因典型用户：**\n' +
          '- `U004812` (银牌会员) | 行为数: 142次 | 总消费: ¥ 48,500\n' +
          '- `U001093` (金牌会员) | 行为数: 115次 | 总消费: ¥ 36,200\n\n' +
          '*💡 联邦洞察：业务库(MySQL)与日志库(MongoDB)的融合分析证实了“互动率”是营收的最佳先行指标。强烈建议基于 ClickHouse 构建实时的数据看板，对高频“收藏”但未下单的用户进行自动化的优惠券促活。*'

      } else {
        sql = `-- demo_mode=true, target_db=${targetDb}\nSELECT 'offline_demo' AS source, ? AS user_query;`
        answer =
          `【离线演示】已收到问题：「${query.trim() || '（空）'}」\n\n` +
          '系统目前处于离线演示状态，未检测到本地引擎。您可以点击上方的三个“快捷示例”来体验本系统。开启真实模式请：关闭侧栏「离线演示模式」。'
      }

      resolve({
        code: 200,
        data: {
          answer,
          metrics: {
            executed_sql: sql,
            retries_triggered: 0,
            final_threshold: 0.8,
            demo_mode: true,
          },
        },
      })
    }, delay)
  })
}
