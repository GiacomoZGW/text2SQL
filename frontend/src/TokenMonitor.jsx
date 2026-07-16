import { Activity, CheckCircle2, Coins, Gauge, RefreshCw, Timer } from 'lucide-react';

function formatNumber(value) {
  return Number(value || 0).toLocaleString('zh-CN');
}

function Metric({ icon: Icon, label, value, suffix = '' }) {
  return (
    <div className="border border-slate-200 bg-white p-4 rounded-lg">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <Icon className="w-4 h-4 text-indigo-500" />
        <span>{label}</span>
      </div>
      <div className="mt-3 text-2xl font-semibold text-slate-900">
        {value}{suffix}
      </div>
    </div>
  );
}

function TokenLineChart({ timeline }) {
  const data = timeline?.length === 1 ? [timeline[0], timeline[0]] : timeline || [];
  const width = 720;
  const height = 220;
  const padding = { top: 18, right: 18, bottom: 28, left: 42 };
  const maxTokens = Math.max(...data.map((item) => item.total_tokens || 0), 1);
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const points = data.map((item, index) => {
    const x = padding.left + (data.length <= 1 ? 0 : index / (data.length - 1)) * chartWidth;
    const y = padding.top + chartHeight - ((item.total_tokens || 0) / maxTokens) * chartHeight;
    return `${x},${y}`;
  }).join(' ');

  if (!data.length) {
    return <div className="h-[220px] flex items-center justify-center text-sm text-slate-400">暂无监控数据</div>;
  }

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-[220px]" role="img" aria-label="Token 消耗趋势">
        {[0, 1, 2, 3].map((index) => {
          const y = padding.top + (chartHeight / 3) * index;
          const value = Math.round(maxTokens - (maxTokens / 3) * index);
          return (
            <g key={index}>
              <line x1={padding.left} x2={width - padding.right} y1={y} y2={y} stroke="#e2e8f0" strokeWidth="1" />
              <text x={padding.left - 8} y={y + 4} textAnchor="end" fill="#94a3b8" fontSize="11">{formatNumber(value)}</text>
            </g>
          );
        })}
        <polyline fill="none" stroke="#4f46e5" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" points={points} />
        {data.map((item, index) => {
          const [x, y] = points.split(' ')[index].split(',');
          return <circle key={`${item.timestamp}-${index}`} cx={x} cy={y} r="4" fill="#4f46e5" />;
        })}
      </svg>
      <div className="flex justify-between text-xs text-slate-400 px-10">
        <span>{data[0].timestamp.slice(11, 16)}</span>
        <span>{data[data.length - 1].timestamp.slice(11, 16)}</span>
      </div>
    </div>
  );
}

export default function TokenMonitor({ summary, loading, error, onRefresh }) {
  const requests = summary?.requests || {};
  const llm = summary?.llm || {};
  const intent = summary?.intent || {};
  const intentClassification = intent.classification || {};
  const intentLlm = intent.llm || {};
  const intentRoutes = intent.routes || [];
  const agentEffectiveness = summary?.agent_effectiveness || [];

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-4 sm:p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        <div className="flex items-center justify-between border-b border-slate-200 pb-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Token 监控</h2>
            <p className="mt-1 text-sm text-slate-500">最近 {summary?.window_hours || 24} 小时</p>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            title="刷新监控数据"
            className="w-9 h-9 inline-flex items-center justify-center border border-slate-200 bg-white text-slate-600 hover:text-indigo-600 disabled:opacity-50 rounded-lg"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {error && <div className="border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 rounded-lg">{error}</div>}

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <Metric icon={Coins} label="总 Token 消耗" value={formatNumber(llm.total_tokens)} />
          <Metric icon={Activity} label="请求数" value={formatNumber(requests.request_count)} />
          <Metric icon={CheckCircle2} label="查询命中率" value={requests.technical_success_rate || 0} suffix="%" />
          <Metric icon={Timer} label="平均模型耗时" value={((llm.average_llm_latency_ms || 0) / 1000).toFixed(2)} suffix="s" />
        </div>

        <section className="border-t border-slate-200 pt-5">
          <div className="flex items-center gap-2 mb-4">
            <Activity className="w-4 h-4 text-indigo-500" />
            <h3 className="text-sm font-semibold text-slate-800">Token 消耗趋势</h3>
          </div>
          <TokenLineChart timeline={summary?.timeline || []} />
        </section>

        <section className="border-t border-slate-200 pt-5">
          <div className="flex items-center gap-2 mb-4">
            <CheckCircle2 className="w-4 h-4 text-indigo-500" />
            <h3 className="text-sm font-semibold text-slate-800">查询质量与生命周期</h3>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            <Metric icon={CheckCircle2} label="SQL 可执行率" value={requests.sql_executable_rate || 0} suffix="%" />
            <Metric icon={CheckCircle2} label="结果正确率" value={requests.result_correct_rate || 0} suffix="%" />
            <Metric icon={Gauge} label="用户满意度" value={requests.satisfaction_rate || 0} suffix="%" />
            <Metric icon={Activity} label="运行中 / 已中止" value={`${formatNumber(requests.running_count)} / ${formatNumber(requests.aborted_count)}`} />
          </div>
          <div className="mt-3 text-xs text-slate-500">
            查询命中率仅以终态请求为分母；SQL、结果和满意度仅以对应已评估样本为分母。
          </div>
        </section>

        <section className="border-t border-slate-200 pt-5">
          <div className="flex items-center gap-2 mb-4">
            <Activity className="w-4 h-4 text-indigo-500" />
            <h3 className="text-sm font-semibold text-slate-800">意图识别质量</h3>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            <Metric icon={Activity} label="已识别请求" value={formatNumber(intentClassification.classified_count)} />
            <Metric icon={CheckCircle2} label="平均置信度" value={(Number(intentClassification.average_confidence || 0) * 100).toFixed(1)} suffix="%" />
            <Metric icon={Gauge} label="触发澄清" value={formatNumber(intentClassification.clarification_count)} />
            <Metric icon={Coins} label="Intent Token" value={formatNumber(intentLlm.total_tokens)} />
            <Metric icon={Timer} label="识别平均耗时" value={(Number(intentClassification.average_latency_ms || 0) / 1000).toFixed(2)} suffix="s" />
            <Metric icon={CheckCircle2} label="澄清后查询成功" value={formatNumber(intentClassification.clarification_followup_success_count)} />
          </div>
          <div className="mt-4 border border-slate-200 bg-white rounded-lg overflow-hidden">
            <div className="grid grid-cols-[1fr_110px] gap-3 bg-slate-50 border-b border-slate-200 px-4 py-3 text-xs text-slate-500">
              <span>Supervisor 路由</span><span>请求数</span>
            </div>
            {intentRoutes.length ? intentRoutes.map((route) => (
              <div key={route.route} className="grid grid-cols-[1fr_110px] gap-3 px-4 py-3 border-b border-slate-100 last:border-b-0 text-sm">
                <span className="font-medium text-slate-700">{route.route}</span>
                <span className="text-slate-500">{formatNumber(route.request_count)}</span>
              </div>
            )) : (
              <div className="px-4 py-6 text-sm text-slate-400">暂无意图路由记录</div>
            )}
          </div>
        </section>

        <section className="border-t border-slate-200 pt-5">
          <div className="flex items-center gap-2 mb-4">
            <Gauge className="w-4 h-4 text-indigo-500" />
            <h3 className="text-sm font-semibold text-slate-800">Agent 完成率</h3>
          </div>
          <div className="border border-slate-200 bg-white rounded-lg overflow-hidden">
            <div className="grid grid-cols-[1fr_110px_110px] gap-3 bg-slate-50 border-b border-slate-200 px-4 py-3 text-xs text-slate-500">
              <span>Agent</span><span>执行次数</span><span>完成率</span>
            </div>
            {agentEffectiveness.length ? agentEffectiveness.map((agent) => (
              <div key={agent.agent} className="grid grid-cols-[1fr_110px_110px] gap-3 px-4 py-3 border-b border-slate-100 last:border-b-0 text-sm">
                <span className="font-medium text-slate-700">{agent.agent}</span>
                <span className="text-slate-500">{formatNumber(agent.event_count)}</span>
                <span className="text-emerald-600">{agent.completion_rate}%</span>
              </div>
            )) : (
              <div className="px-4 py-8 text-sm text-slate-400">暂无 Agent 执行记录</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
