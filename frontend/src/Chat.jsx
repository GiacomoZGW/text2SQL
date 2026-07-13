import React, { useState, useEffect, useRef } from 'react';
import {
  Send, Database, LayoutDashboard, Settings, Loader2,
  Code2, TerminalSquare, AlertCircle, CheckCircle2, User, Bot, Sparkles, Server, FileJson, BarChart3, ShieldCheck, BrainCircuit, Pause, RotateCcw, Trash2
} from 'lucide-react';
import { fetchDemoQuery } from './demoMock.js';
import TokenMonitor from './TokenMonitor.jsx';

const DEMO_STORAGE_KEY = 'text2sql_offline_demo';
const CONVERSATION_STORAGE_KEY = 'text2sql_conversation_id';
const USER_ID = 'test_user_001';

function readDemoModeInitial() {
  try {
    const v = sessionStorage.getItem(DEMO_STORAGE_KEY);
    if (v === '1') return true;
    if (v === '0') return false;
  } catch {
    /* private mode */
  }
  return import.meta.env.VITE_DEFAULT_DEMO_MODE === 'true';
}

function welcomeContent(isDemo) {
  if (isDemo) {
    return '你好！当前为 **离线演示模式**。\n\n无需启动后端即可体验界面与模拟问答流程；返回的 SQL 与分析内容为**前端内置样例**，不代表真实库查询结果。\n\n关闭侧栏「离线演示模式」后，可连接真实数据库与 Agent。';
  }
  return '你好！我是 Data Agent。\n\n我已经连接到了底层的真实数据库。\n您可以直接用自然语言问我关于数据的问题，我会自动为您**编写SQL**、**执行查询**并**生成分析报告**。';
}

/** 开发：Vite 代理 /api。生产：与页面同域（便于 FastAPI 一体托管）或由 VITE_API_BASE 指定 */
function apiUrl(path) {
  if (import.meta.env.DEV) return path;
  const base = import.meta.env.VITE_API_BASE;
  if (base !== undefined && base !== '') {
    return `${String(base).replace(/\/$/, '')}${path}`;
  }
  if (typeof window !== 'undefined') {
    return `${window.location.origin}${path}`;
  }
  return `http://127.0.0.1:8000${path}`;
}

function readConversationId() {
  try {
    const current = sessionStorage.getItem(CONVERSATION_STORAGE_KEY);
    if (current) return current;
    const next = globalThis.crypto?.randomUUID?.() || `conversation-${Date.now()}`;
    sessionStorage.setItem(CONVERSATION_STORAGE_KEY, next);
    return next;
  } catch {
    return `conversation-${Date.now()}`;
  }
}

function queryUrl() {
  return apiUrl('/api/v1/query');
}

const Chat = () => {
  const [demoMode, setDemoMode] = useState(readDemoModeInitial);
  const [messages, setMessages] = useState(() => [
    {
      id: 1,
      role: 'ai',
      content: welcomeContent(readDemoModeInitial()),
      sql: null,
      status: 'completed'
    }
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [pauseAvailable, setPauseAvailable] = useState(false);
  const [dataSources, setDataSources] = useState([]);
  const [dataSourceId, setDataSourceId] = useState('');
  const [dataSourceError, setDataSourceError] = useState('');
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [memoryUpdating, setMemoryUpdating] = useState(false);
  const [clarificationParentRequestId, setClarificationParentRequestId] = useState('');
  const [conversationId] = useState(readConversationId);
  const [activeView, setActiveView] = useState('workspace');
  const [monitorSummary, setMonitorSummary] = useState(null);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [monitorError, setMonitorError] = useState('');
  const messagesEndRef = useRef(null);
  const abortControllerRef = useRef(null);
  const activeRequestIdRef = useRef('');
  const pauseRequestedRef = useRef(false);
  const processingTimerIdsRef = useRef([]);
  const selectedDataSource = dataSources.find((source) => source.id === dataSourceId);

  const loadMonitorSummary = async () => {
    setMonitorLoading(true);
    setMonitorError('');
    try {
      const response = await fetch(apiUrl('/api/v1/observability/summary?window_hours=24'));
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const payload = await response.json();
      if (payload.code !== 200) throw new Error('监控接口返回异常');
      setMonitorSummary(payload.data);
    } catch (error) {
      const paused = error.name === 'AbortError';
      setMonitorError(`无法加载监控数据：${error.message}`);
    } finally {
      setMonitorLoading(false);
    }
  };

  const loadDataSources = async () => {
    setDataSourceError('');
    try {
      const response = await fetch(apiUrl('/api/v1/data-sources'));
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const payload = await response.json();
      if (payload.code !== 200 || !Array.isArray(payload.data)) throw new Error('数据源接口返回异常');
      const preferenceResponse = await fetch(apiUrl(`/api/v1/memory/preferences/${USER_ID}`));
      const preferencePayload = preferenceResponse.ok ? await preferenceResponse.json() : { data: {} };
      const preferredSourceId = preferencePayload.data?.default_data_source_id;
      setMemoryEnabled(preferencePayload.data?.memory_enabled !== false);
      setDataSources(payload.data);
      setDataSourceId((current) => {
        if (payload.data.some((source) => source.id === preferredSourceId)) return preferredSourceId;
        return payload.data.some((source) => source.id === current) ? current : (payload.data[0]?.id || '');
      });
    } catch (error) {
      setDataSourceError(`无法加载数据源：${error.message}`);
    }
  };

  const updateMemoryEnabled = async (next) => {
    setMemoryUpdating(true);
    try {
      const response = await fetch(apiUrl(`/api/v1/memory/preferences/${USER_ID}`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ memory_enabled: next })
      });
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const payload = await response.json();
      if (payload.code !== 200) throw new Error('记忆设置保存失败');
      setMemoryEnabled(payload.data.memory_enabled);
    } catch (error) {
      setDataSourceError(`无法更新记忆设置：${error.message}`);
    } finally {
      setMemoryUpdating(false);
    }
  };

  const clearUserMemory = async () => {
    if (!window.confirm('确认删除当前用户的会话、语义和偏好记忆吗？')) return;
    setMemoryUpdating(true);
    try {
      const response = await fetch(apiUrl(`/api/v1/memory/${USER_ID}`), { method: 'DELETE' });
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      setClarificationParentRequestId('');
      setMemoryEnabled(true);
      await loadDataSources();
    } catch (error) {
      setDataSourceError(`无法删除记忆：${error.message}`);
    } finally {
      setMemoryUpdating(false);
    }
  };

  const clearProcessingTimers = () => {
    processingTimerIdsRef.current.forEach((timerId) => clearTimeout(timerId));
    processingTimerIdsRef.current = [];
  };

  const handlePause = () => {
    pauseRequestedRef.current = true;
    const requestId = activeRequestIdRef.current;
    if (requestId) {
      fetch(apiUrl(`/api/v1/requests/${requestId}/pause`), { method: 'POST' }).catch(() => {});
    }
    abortControllerRef.current?.abort();
    clearProcessingTimers();
    setPauseAvailable(false);
    setMessages((prev) => prev.map((message) => message.status === 'thinking' ? {
      ...message,
      status: 'paused',
      content: '当前请求已暂停。',
      steps: []
    } : message));
    setIsProcessing(false);
  };

  const persistDemoMode = (next) => {
    setDemoMode(next);
    try {
      sessionStorage.setItem(DEMO_STORAGE_KEY, next ? '1' : '0');
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    setMessages((prev) => {
      if (prev.length !== 1 || prev[0].role !== 'ai' || prev[0].id !== 1) return prev;
      return [{ ...prev[0], content: welcomeContent(demoMode) }];
    });
  }, [demoMode]);

  useEffect(() => {
    loadDataSources();
  }, [demoMode]);

  const quickExamples = [
    { icon: "📊", title: "统计各商品类别的销售总额", prompt: "统计一下各商品类别的销售总额，并按金额降序排列" },
    { icon: "🏙️", title: "电子产品购买用户的城市分布", prompt: "查找购买了电子产品的用户城市分布情况" },
    { icon: "👥", title: "跨库分析：行为与购买转化（需选择多元联邦引擎）", prompt: "跨库分析：去行为日志库(mongo)查一下最常产生'加购'或'收藏'行为的用户，他们在业务核心库(mysql)里的订单总金额是多少？" }
  ];

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    if (activeView === 'monitor') loadMonitorSummary();
  }, [activeView]);

  const handleSend = async (overrideText = null) => {
    const userText = overrideText || inputValue.trim();
    if (!userText || isProcessing) return;

    setInputValue('');
    setIsProcessing(true);
    setPauseAvailable(false);
    pauseRequestedRef.current = false;
    clearProcessingTimers();
    const requestId = globalThis.crypto?.randomUUID?.() || `request-${Date.now()}`;
    activeRequestIdRef.current = requestId;
    abortControllerRef.current = new AbortController();

    const newUserMsg = { id: Date.now(), role: 'user', content: userText };

    const aiMsgId = Date.now() + 1;
    const pendingAiMsg = {
      id: aiMsgId,
      role: 'ai',
      content: '',
      sql: null,
      status: 'thinking',
      requestText: userText,
      steps: [
        { id: 'supervisor', name: '查询规划 Supervisor', status: 'pending' },
        { id: 'schema', name: '实时 Schema Agent', status: 'pending' },
        { id: 'retrieval', name: 'Chroma Schema 检索', status: 'pending' },
        { id: 'sql', name: 'SQL 生成 Agent', status: 'pending' },
        { id: 'validator', name: 'SQL 校验 Agent', status: 'pending' },
        { id: 'reviewer', name: 'SQL 评审 Agent', status: 'pending' },
        { id: 'executor', name: '只读执行 Agent', status: 'pending' },
        { id: 'analyst', name: '数据分析 Agent', status: 'pending' }
      ]
    };

    setMessages(prev => [...prev, newUserMsg, pendingAiMsg]);

    const updateStep = (stepId, status) => {
      setMessages(prev => prev.map(msg => {
        if (msg.id === aiMsgId) {
          return {
            ...msg,
            steps: msg.steps?.map(s => s.id === stepId ? { ...s, status } : s)
          };
        }
        return msg;
      }));
    };

    const scheduleStep = (callback, delay) => {
      const timerId = setTimeout(callback, delay);
      processingTimerIdsRef.current.push(timerId);
    };

    // Keep the send control stable: the pause control replaces the loader only
    // after the request has been running long enough to make pausing useful.
    scheduleStep(() => setPauseAvailable(true), 2000);

    try {
      updateStep('supervisor', 'running');
      scheduleStep(() => { updateStep('supervisor', 'completed'); updateStep('schema', 'running'); }, 400);
      scheduleStep(() => { updateStep('schema', 'completed'); updateStep('retrieval', 'running'); }, 900);
      scheduleStep(() => { updateStep('retrieval', 'completed'); updateStep('sql', 'running'); }, 1500);
      scheduleStep(() => { updateStep('sql', 'completed'); updateStep('validator', 'running'); }, 2300);
      scheduleStep(() => { updateStep('validator', 'completed'); updateStep('reviewer', 'running'); }, 2900);
      scheduleStep(() => { updateStep('reviewer', 'completed'); updateStep('executor', 'running'); }, 3400);

      let resData;
      if (demoMode) {
        const demoTarget = dataSources.find((source) => source.id === dataSourceId)?.engine === 'duckdb' ? 'federated' : 'sqlite';
        resData = await fetchDemoQuery(userText, demoTarget);
      } else {
        const response = await fetch(queryUrl(), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: USER_ID,
            query: userText,
            data_source_id: dataSourceId || undefined,
            conversation_id: conversationId,
            clarification_parent_request_id: clarificationParentRequestId || undefined,
            client_request_id: requestId
          }),
          signal: abortControllerRef.current.signal
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        resData = await response.json();
      }

      updateStep('sql', 'completed');
      updateStep('validator', 'completed');
      updateStep('reviewer', 'completed');
      updateStep('executor', 'completed');
      updateStep('analyst', 'completed');

      if (resData.code === 200) {
        const intent = resData.data.metrics?.intent;
        const requestId = resData.data.metrics?.observability_request_id;
        if (intent?.needs_clarification && requestId) {
          setClarificationParentRequestId(requestId);
        } else if (intent?.name === 'text_to_sql' || intent?.name === 'data_analysis') {
          setClarificationParentRequestId('');
        }
        setMessages(prev => prev.map(msg =>
          msg.id === aiMsgId ? {
            ...msg,
            status: 'completed',
            content: resData.data.answer,
            sql: resData.data.metrics?.executed_sql || null,
            steps: []
          } : msg
        ));
      } else {
        throw new Error(resData.data?.answer || "后端返回未知错误");
      }

    } catch (error) {
      console.error("API 调用失败:", error);
      const paused = pauseRequestedRef.current || error.name === 'AbortError';
      setMessages(prev => prev.map(msg =>
        msg.id === aiMsgId ? {
          ...msg,
          status: paused ? 'paused' : 'error',
          content: paused
            ? '当前请求已暂停。'
            : `执行失败。\n错误详情: ${error.message}\n💡 可开启侧栏「离线演示模式」体验界面；如需体验完整 LLM 生成与实时查询，请克隆仓库并运行 python api/main.py 启动后端。`,
          steps: []
        } : msg
      ));
    } finally {
      clearProcessingTimers();
      setPauseAvailable(false);
      abortControllerRef.current = null;
      activeRequestIdRef.current = '';
      pauseRequestedRef.current = false;
      setIsProcessing(false);
    }
  };

  const getStepIcon = (stepId, status) => {
    if (status === 'running') return <Loader2 className="w-4 h-4 animate-spin text-blue-500" />;
    if (status === 'completed') return <CheckCircle2 className="w-4 h-4 text-emerald-500" />;

    switch (stepId) {
      case 'supervisor': return <Bot className="w-4 h-4 text-slate-400" />;
      case 'schema': return <Database className="w-4 h-4 text-slate-400" />;
      case 'retrieval': return <Database className="w-4 h-4 text-slate-400" />;
      case 'sql': return <Code2 className="w-4 h-4 text-slate-400" />;
      case 'validator': return <ShieldCheck className="w-4 h-4 text-slate-400" />;
      case 'reviewer': return <ShieldCheck className="w-4 h-4 text-slate-400" />;
      case 'executor': return <FileJson className="w-4 h-4 text-slate-400" />;
      case 'analyst': return <BarChart3 className="w-4 h-4 text-slate-400" />;
      default: return <div className="w-4 h-4 rounded-full bg-slate-200" />;
    }
  };

  return (
    <div className="flex h-screen bg-slate-50 font-sans text-slate-800">

      <div className="w-72 bg-slate-900 flex flex-col text-white shadow-xl z-20">
        <div className="p-6 border-b border-slate-800 flex items-center space-x-3">
          <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-500/30">
            <Database className="w-5 h-5 text-white" />
          </div>
          <div>
            <span className="font-bold text-lg tracking-wide block leading-tight">Data Agent</span>
            <span className="text-[10px] text-blue-400 font-mono tracking-wider uppercase">Enterprise Edition</span>
          </div>
        </div>

        <div className="p-6 flex-1 overflow-y-auto">
          <div className="mb-8">
            <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-widest mb-3 flex items-center">
              <Settings className="w-3.5 h-3.5 mr-1.5"/> 配置中心
            </h3>
            <label className="block text-sm font-medium text-slate-300 mb-2">选择目标数据库引擎</label>
            <select
              value={dataSourceId}
              onChange={(e) => setDataSourceId(e.target.value)}
              disabled={!dataSources.length && !demoMode}
              className="w-full bg-slate-800/80 border border-slate-700 text-slate-200 text-sm rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 block p-3 outline-none transition-all cursor-pointer hover:bg-slate-800"
            >
              {dataSources.map((source) => (
                <option key={source.id} value={source.id}>{source.name} ({source.engine})</option>
              ))}
            </select>
            {dataSourceError && <p className="mt-2 text-xs text-amber-300">{dataSourceError}</p>}
          </div>

          <div className="mb-8">
            <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-widest mb-3 flex items-center">
              <BrainCircuit className="w-3.5 h-3.5 mr-1.5"/> 记忆
            </h3>
            <div className="flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-800/80 p-3">
              <label className="flex flex-1 cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  checked={memoryEnabled}
                  disabled={memoryUpdating}
                  onChange={(e) => updateMemoryEnabled(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-600 text-indigo-500 focus:ring-blue-500"
                />
                <span className="text-sm font-medium text-slate-200">启用会话与语义记忆</span>
              </label>
              <button
                type="button"
                onClick={clearUserMemory}
                disabled={memoryUpdating}
                title="删除当前用户的记忆"
                className="w-9 h-9 inline-flex items-center justify-center rounded-lg border border-slate-600 text-slate-300 hover:border-red-400 hover:text-red-300 disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="mb-8">
            <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-widest mb-3 flex items-center">
              <Sparkles className="w-3.5 h-3.5 mr-1.5"/> 运行模式
            </h3>
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-700 bg-slate-800/80 p-3 transition hover:bg-slate-800">
              <input
                type="checkbox"
                checked={demoMode}
                onChange={(e) => persistDemoMode(e.target.checked)}
                className="mt-1 h-4 w-4 rounded border-slate-600 text-indigo-500 focus:ring-blue-500"
              />
              <span>
                <span className="block text-sm font-medium text-slate-200">离线演示模式 (Demo)</span>
                <span className="mt-1 block text-xs text-slate-500 leading-relaxed">不请求后端，使用内置模拟回答与示例 SQL，适合路演与无网络环境。</span>
              </span>
            </label>
          </div>

          <div className="mb-6">
            <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-widest mb-3 flex items-center">
              <Server className="w-3.5 h-3.5 mr-1.5"/> 系统监控状态
            </h3>
            <div className={`rounded-xl border p-4 text-sm flex items-start space-x-3 ${
              demoMode
                ? 'bg-slate-800/40 border-amber-500/30'
                : 'bg-slate-800/40 border-emerald-500/20'
            }`}>
              <div className="mt-1">
                <span className="flex h-2.5 w-2.5 relative">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${demoMode ? 'bg-amber-400' : 'bg-emerald-400'}`}></span>
                  <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${demoMode ? 'bg-amber-500' : 'bg-emerald-500'}`}></span>
                </span>
              </div>
              <div>
                <p className={`font-medium ${demoMode ? 'text-amber-400' : 'text-emerald-400'}`}>
                  {demoMode ? '离线演示 · 前端模拟' : 'LangGraph 引擎在线'}
                </p>
                <p className="text-xs mt-1.5 text-slate-400 leading-relaxed">
                  {demoMode
                    ? '未连接 API。关闭本模式并启动后端后可查询真实 ecommerce 库。'
                    : '已配置为请求后端 Agent。请保持 python api/main.py 运行。'}
                </p>
                <div className="mt-3 inline-flex items-center px-2 py-1 rounded bg-slate-800/80 border border-slate-700 text-[10px] text-slate-400 font-mono">
                  {demoMode ? 'demo_mode | 无网络依赖' : 'v2.0.1 | 动态容错开启'}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 flex flex-col relative bg-white">
        <header className="h-16 bg-white border-b border-slate-100 flex items-center px-8 shadow-sm z-10 justify-between">
          <div className="flex items-center gap-1 border border-slate-200 bg-slate-50 p-1 rounded-lg" role="tablist" aria-label="工作台视图">
            <button
              type="button"
              role="tab"
              aria-selected={activeView === 'workspace'}
              onClick={() => setActiveView('workspace')}
              className={`inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-md ${activeView === 'workspace' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              <LayoutDashboard className="w-4 h-4" />
              查询工作台
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeView === 'monitor'}
              onClick={() => setActiveView('monitor')}
              className={`inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-md ${activeView === 'monitor' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              <BarChart3 className="w-4 h-4" />
              Token 监控
            </button>
          </div>
          <div className="flex items-center space-x-2 bg-indigo-50 text-indigo-600 px-3 py-1.5 rounded-full text-xs font-medium border border-indigo-100">
            <Sparkles className="w-3.5 h-3.5" />
            <span>AI 自动 SQL 生成</span>
          </div>
        </header>

        {activeView === 'workspace' ? (
          <>
        <div className="flex-1 overflow-y-auto p-4 sm:p-8 space-y-6 sm:space-y-8 bg-slate-50">
          {messages.map((msg, index) => (
            <div key={msg.id} className="flex flex-col">

              <div className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-4xl flex ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'} items-start w-full`}>

                  <div className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center shadow-sm ${msg.role === 'user' ? 'bg-indigo-600 ml-4' : 'bg-slate-800 mr-4'}`}>
                    {msg.role === 'user' ?
                      <User className="w-5 h-5 text-white" /> :
                      <Bot className="w-5 h-5 text-white" />
                    }
                  </div>

                  <div className={`flex flex-col space-y-3 w-[90%] sm:w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>

                    {msg.role === 'user' && (
                      <div className="bg-indigo-600 text-white px-6 py-4 rounded-2xl rounded-tr-none shadow-md text-[15px] leading-relaxed">
                        {msg.content}
                      </div>
                    )}

                    {msg.role === 'ai' && (
                      <div className="w-full space-y-4">

                        {index === 0 && (
                          <div className="bg-white border border-slate-200 px-6 py-5 rounded-2xl rounded-tl-none shadow-sm text-[15px] text-slate-800 leading-relaxed whitespace-pre-wrap">
                            {msg.content}

                            <div className="mt-6 pt-5 border-t border-slate-100">
                              <h4 className="text-sm font-semibold text-slate-500 mb-4 flex items-center">
                                <Sparkles className="w-4 h-4 mr-1.5 text-amber-500"/> 试试这些快速示例：
                              </h4>
                              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                {quickExamples.map((ex, i) => (
                                  <button
                                    key={i}
                                    onClick={() => handleSend(ex.prompt)}
                                    disabled={isProcessing}
                                    className="text-left bg-slate-50 hover:bg-indigo-50 border border-slate-200 hover:border-indigo-200 p-4 rounded-xl transition-all duration-200 group disabled:opacity-50 disabled:cursor-not-allowed"
                                  >
                                    <div className="text-lg mb-2">{ex.icon}</div>
                                    <div className="text-sm font-medium text-slate-700 group-hover:text-indigo-700 leading-snug">{ex.title}</div>
                                  </button>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}

                        {msg.status === 'thinking' && msg.steps && msg.steps.length > 0 && (
                          <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm w-full max-w-md rounded-tl-none">
                            <h4 className="text-xs font-bold text-slate-400 mb-4 uppercase tracking-wider flex items-center">
                              <LayoutDashboard className="w-3.5 h-3.5 mr-2"/>
                              Agent 联邦图计算引擎正在处理
                            </h4>
                            <div className="space-y-4">
                              {msg.steps.map((step) => (
                                <div key={step.id} className={`flex items-center space-x-3 text-sm transition-all duration-500 ${step.status === 'pending' ? 'opacity-30 translate-x-2' : 'opacity-100 translate-x-0'}`}>
                                  {getStepIcon(step.id, step.status)}
                                  <span className={`font-medium ${step.status === 'running' ? 'text-blue-600' : 'text-slate-600'}`}>
                                    {step.name}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {msg.status === 'error' && (
                          <div className="flex items-start space-x-3 text-red-600 bg-red-50 px-6 py-5 rounded-2xl rounded-tl-none border border-red-100 whitespace-pre-wrap shadow-sm">
                            <AlertCircle className="w-5 h-5 mt-0.5 flex-shrink-0" />
                            <span className="text-[15px] leading-relaxed">{msg.content}</span>
                          </div>
                        )}

                        {msg.status === 'paused' && (
                          <div className="flex items-start gap-3 text-amber-700 bg-amber-50 px-6 py-5 rounded-2xl rounded-tl-none border border-amber-200 shadow-sm">
                            <Pause className="w-5 h-5 mt-0.5 flex-shrink-0" />
                            <div className="min-w-0">
                              <p className="text-[15px] leading-relaxed">当前请求已暂停。</p>
                              <button
                                type="button"
                                onClick={() => handleSend(msg.requestText)}
                                disabled={isProcessing || !msg.requestText}
                                className="mt-3 inline-flex h-9 items-center gap-2 rounded-lg border border-amber-300 bg-white px-3 text-sm font-medium text-amber-800 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <RotateCcw className="w-4 h-4" />
                                重试
                              </button>
                            </div>
                          </div>
                        )}

                        {msg.status === 'completed' && msg.sql && (
                          <div className="bg-[#0f172a] rounded-2xl rounded-tl-none overflow-hidden shadow-md border border-slate-800">
                            <div className="bg-[#1e293b] px-5 py-2.5 flex items-center justify-between border-b border-slate-700/50">
                              <div className="flex items-center text-slate-300 text-xs font-mono">
                                <TerminalSquare className="w-4 h-4 mr-2 text-indigo-400" />
                                引擎底层执行 SQL
                              </div>
                              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                            </div>
                            <div className="p-5 overflow-x-auto">
                              <pre className="text-emerald-400 font-mono text-sm leading-relaxed">
                                <code>{msg.sql}</code>
                              </pre>
                            </div>
                          </div>
                        )}

                        {msg.status === 'completed' && index > 0 && (
                          <div className="bg-white border border-slate-200 px-6 py-5 rounded-2xl shadow-sm text-[15px] text-slate-800 leading-relaxed whitespace-pre-wrap">
                            {msg.content}
                          </div>
                        )}

                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        <div className="p-4 sm:p-6 bg-white border-t border-slate-100 shadow-[0_-10px_40px_-15px_rgba(0,0,0,0.05)] z-20">
          <div className="max-w-4xl mx-auto relative flex items-center">
            <textarea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="在此输入您的数据查询需求，例如：'分析一下上个月的订单转化率'..."
              className="w-full bg-slate-50 border border-slate-200 rounded-2xl pl-5 pr-16 py-4 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 focus:bg-white resize-none h-[64px] shadow-inner text-[15px] transition-all"
              disabled={isProcessing}
            />
            <button
              type="button"
              onClick={isProcessing ? handlePause : () => handleSend()}
              disabled={isProcessing ? !pauseAvailable : !inputValue.trim()}
              title={isProcessing ? (pauseAvailable ? '暂停当前请求' : '正在生成') : '发送请求'}
              aria-label={isProcessing ? (pauseAvailable ? '暂停当前请求' : '正在生成') : '发送请求'}
              className={`absolute right-2 bottom-2 p-3 rounded-xl transition-all duration-200 flex items-center justify-center ${
                isProcessing
                  ? (pauseAvailable
                    ? 'bg-amber-500 text-white hover:bg-amber-600 shadow-md'
                    : 'bg-slate-200 text-slate-400 cursor-wait')
                  : (!inputValue.trim()
                    ? 'bg-slate-200 text-slate-400 cursor-not-allowed'
                    : 'bg-indigo-600 text-white hover:bg-indigo-700 shadow-md hover:shadow-lg transform hover:-translate-y-0.5 active:translate-y-0')
              }`}
            >
              {isProcessing
                ? (pauseAvailable ? <Pause className="w-5 h-5" /> : <Loader2 className="w-5 h-5 animate-spin" />)
                : <Send className="w-5 h-5 ml-0.5" />}
            </button>
          </div>
          <div className="text-center mt-3 text-xs text-slate-400 flex flex-wrap items-center justify-center gap-x-3 gap-y-1">
            <div className="flex items-center"><Code2 className="w-3.5 h-3.5 mr-1" /> AI 模型: Qwen3.5-flash</div>
            <span className="text-slate-300 hidden sm:inline">|</span>
            <div className="flex items-center">
              <Database className="w-3.5 h-3.5 mr-1" />
              {demoMode ? '离线演示 · 模拟数据' : (selectedDataSource ? `${selectedDataSource.name} · ${selectedDataSource.status}` : '正在加载数据源')}
            </div>
          </div>
        </div>
          </>
        ) : (
          <TokenMonitor
            summary={monitorSummary}
            loading={monitorLoading}
            error={monitorError}
            onRefresh={loadMonitorSummary}
          />
        )}
      </div>
    </div>
  );
};

export default Chat;
