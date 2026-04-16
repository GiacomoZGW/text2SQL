import React, { useState } from 'react';
import {
  Github,
  Mail,
  Terminal,
  Cpu,
  Layers,
  ArrowUpRight,
  Code2,
  Database,
  ChevronLeft,
  Sparkles,
} from 'lucide-react';
import Chat from './Chat.jsx';

const App = () => {
  const [page, setPage] = useState('home');
  const [activeFilter, setActiveFilter] = useState('全部');

  const profile = {
    name: 'Guiwei Zhang',
    headline: '专注于大模型编排、技术自动化与效能提升',
    bio: '毕业于华南农业大学。具备出色的英语能力（CET-6）与 Python 开发功底。擅长利用 LangChain/LangGraph、Coze/Dify 以及影刀 RPA 等工具，打通业务数据孤岛，构建从端到端的高效自动化流水线。',
    skills: [
      'Python',
      'LangGraph',
      'React',
      'Text2SQL',
      'FastAPI',
      'RAG',
      'Milvus',
      '影刀 RPA',
      'Coze',
      'SQLite',
      'Prompt Engineering',
    ],
  };

  const projects = [
    {
      id: 1,
      title: '联邦分析 Data Agent 工作台',
      tags: ['AI Agent', 'Fullstack', 'LangGraph'],
      icon: <Terminal className="w-5 h-5" />,
      description:
        '本仓库中的 Text2SQL 联邦查询演示：基于 LangGraph 多智能体协同，对接 SQLite 示例库，支持自然语言提问、SQL 生成与结果分析。下方可进入与本站集成的交互式前端。',
      link: 'https://github.com',
      hasDemo: true,
    },
    {
      id: 2,
      title: '亚马逊商品描述 AI 自动化处理流',
      tags: ['LLM', 'RPA', 'Coze'],
      icon: <Cpu className="w-5 h-5" />,
      description:
        '独立开发基于影刀的自动化工作流，实时抓取 Google Finance 汇率波动数据，并结合业务逻辑自动将预警信息精准推送到微信工作群。深度接入 Coze API，自动批量提取并重写商品描述，将清洗后的高质量文案无缝同步至飞书多维表格，极大提升运营人员上架效率。',
      link: '#',
      hasDemo: false,
    },
  ];

  const filters = ['全部', 'AI Agent', 'RPA'];

  const filteredProjects =
    activeFilter === '全部'
      ? projects
      : projects.filter((p) => p.tags.includes(activeFilter));

  if (page === 'text2sql') {
    return (
      <div className="relative min-h-screen">
        <button
          type="button"
          onClick={() => setPage('home')}
          className="fixed top-4 left-4 z-[100] inline-flex items-center gap-2 rounded-full border border-zinc-200 bg-white/95 px-4 py-2 text-sm font-medium text-zinc-800 shadow-md backdrop-blur-sm transition hover:bg-zinc-50"
        >
          <ChevronLeft className="h-4 w-4" />
          返回个人站
        </button>
        <Chat />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#fafafa] font-sans text-zinc-900 selection:bg-zinc-900 selection:text-white">

      <nav className="fixed top-0 left-0 right-0 z-50 border-b border-zinc-200 bg-[#fafafa]/80 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-6">
          <span className="text-lg font-bold tracking-tight">Portfolio.</span>
          <div className="flex items-center gap-3 sm:gap-4">
            <button
              type="button"
              onClick={() => setPage('text2sql')}
              className="inline-flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-800 shadow-sm transition hover:border-zinc-400 hover:bg-zinc-50"
            >
              <Database className="h-4 w-4 text-indigo-600" />
              <span className="hidden sm:inline">Text2SQL 演示</span>
              <span className="sm:hidden">演示</span>
            </button>
            <a
              href="mailto:your.email@example.com"
              className="text-zinc-500 transition-colors hover:text-zinc-900"
              aria-label="Email"
            >
              <Mail className="h-5 w-5" />
            </a>
            <a
              href="https://github.com"
              target="_blank"
              rel="noreferrer"
              className="text-zinc-500 transition-colors hover:text-zinc-900"
              aria-label="GitHub"
            >
              <Github className="h-5 w-5" />
            </a>
          </div>
        </div>
      </nav>

      <main className="mx-auto max-w-5xl px-6 pb-24 pt-32">

        <section className="animate-fade-in-up mb-24">
          <div className="mb-6 inline-flex items-center space-x-2 rounded-full border border-zinc-200 bg-zinc-100 px-3 py-1 text-sm font-medium text-zinc-600">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            <span>Available for new opportunities</span>
          </div>

          <h1 className="mb-6 text-4xl font-extrabold leading-[1.1] tracking-tight text-zinc-900 sm:text-5xl md:text-6xl">
            Hello, 我是{' '}
            <span className="underline decoration-4 decoration-zinc-300 underline-offset-8">
              {profile.name}
            </span>
            .<br />
            {profile.headline}
          </h1>

          <p className="mb-10 max-w-3xl text-lg leading-relaxed text-zinc-600">{profile.bio}</p>

          <div className="mb-8 flex flex-wrap gap-2">
            {profile.skills.map((skill, index) => (
              <span
                key={index}
                className="cursor-default rounded-lg border border-zinc-200 bg-white px-4 py-2 text-sm font-medium text-zinc-700 shadow-sm transition-colors hover:border-zinc-400"
              >
                {skill}
              </span>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setPage('text2sql')}
            className="inline-flex items-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50 px-5 py-3 text-sm font-semibold text-indigo-900 shadow-sm transition hover:bg-indigo-100"
          >
            <Sparkles className="h-4 w-4 text-indigo-600" />
            体验站内 Text2SQL 联邦分析 Demo
          </button>
        </section>

        <section>
          <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
            <h2 className="flex items-center text-2xl font-bold tracking-tight">
              <Code2 className="mr-2 h-6 w-6 text-zinc-400" />
              精选项目 (Featured Projects)
            </h2>

            <div className="flex space-x-1 rounded-lg border border-zinc-200 bg-zinc-100 p-1">
              {filters.map((filter) => (
                <button
                  key={filter}
                  type="button"
                  onClick={() => setActiveFilter(filter)}
                  className={`rounded-md px-4 py-1.5 text-sm font-medium transition-all duration-200 ${
                    activeFilter === filter
                      ? 'bg-white text-zinc-900 shadow-sm'
                      : 'text-zinc-500 hover:bg-zinc-200/50 hover:text-zinc-700'
                  }`}
                >
                  {filter}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {filteredProjects.map((project) => (
              <div
                key={project.id}
                className="group relative flex h-full flex-col rounded-2xl border border-zinc-200 bg-white p-6 transition-all duration-300 hover:border-zinc-900 hover:shadow-[0_8px_30px_rgb(0,0,0,0.04)] sm:p-8"
              >
                <div className="mb-6 flex items-start justify-between">
                  <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-zinc-100 bg-zinc-50 text-zinc-700 transition-all duration-300 group-hover:scale-110 group-hover:bg-zinc-900 group-hover:text-white">
                    {project.icon}
                  </div>
                  {project.hasDemo ? (
                    <button
                      type="button"
                      onClick={() => setPage('text2sql')}
                      className="text-zinc-400 transition-colors hover:text-indigo-600"
                      aria-label="打开 Text2SQL 演示"
                    >
                      <ArrowUpRight className="h-5 w-5 opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100 translate-y-1" />
                    </button>
                  ) : (
                    <a
                      href={project.link}
                      className="text-zinc-400 transition-colors hover:text-zinc-900"
                    >
                      <ArrowUpRight className="h-5 w-5 opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100 translate-y-1" />
                    </a>
                  )}
                </div>

                <h3 className="mb-3 text-xl font-bold text-zinc-900 transition-colors group-hover:text-black">
                  {project.title}
                </h3>

                <p className="mb-6 flex-1 text-sm leading-relaxed text-zinc-500">
                  {project.description}
                </p>

                {project.hasDemo && (
                  <button
                    type="button"
                    onClick={() => setPage('text2sql')}
                    className="mb-6 w-full rounded-xl bg-zinc-900 py-2.5 text-sm font-semibold text-white transition hover:bg-zinc-800"
                  >
                    进入联邦分析工作台
                  </button>
                )}

                <div className="mt-auto flex flex-wrap gap-2">
                  {project.tags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-md bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-600"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

      </main>

      <footer className="border-t border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-col items-center justify-between px-6 py-12 sm:flex-row">
          <p className="mb-4 text-sm text-zinc-500 sm:mb-0">
            © {new Date().getFullYear()} By AI & RPA Developer. All rights reserved.
          </p>
          <div className="flex space-x-6">
            <a href="#" className="text-sm font-medium text-zinc-500 transition-colors hover:text-zinc-900">
              Resume (PDF)
            </a>
            <a href="#" className="text-sm font-medium text-zinc-500 transition-colors hover:text-zinc-900">
              LinkedIn
            </a>
          </div>
        </div>
      </footer>

      <style
        dangerouslySetInnerHTML={{
          __html: `
        @keyframes fadeInUp {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-in-up {
          animation: fadeInUp 0.8s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
      `,
        }}
      />
    </div>
  );
};

export default App;
