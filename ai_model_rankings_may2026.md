========================================================================
    AI MODEL RANKINGS & BENCHMARKS - May 2026 Research Report
========================================================================

Data sourced from: Arena-Hard-Auto (LMSYS/LMArena), SWE-bench, 
SWE-agent, Aider, LiveCodeBench documentation repositories.
Note: Some data reflects latest available (up to April 2025 releases).

========================================================================
1. ARENA-HARD-AUTO LEADERBOARD (LMSYS)
   Hard Prompts + Style Control, Gemini-2.5 as Judge
========================================================================

Rank  Model                                    Score   95% CI
----  ----------------------------------------  -----  -----------
 1    o3-2025-04-16                             85.9%  (-0.8/+0.9)
 2    o4-mini-2025-04-16-high                   79.1%  (-1.4/+1.2)
 3    gemini-2.5 (Pro)                          79.0%  (-2.1/+1.8)
 4    o4-mini-2025-04-16                        74.6%  (-1.8/+1.6)
 5    gemini-2.5-flash                          68.6%  (-1.6/+1.6)
 6    o3-mini-2025-01-31-high                   66.1%  (-1.5/+2.1)
 7    o1-2024-12-17-high                        61.0%  (-2.0/+2.1)
 8    claude-3-7-sonnet-20250219-thinking-16k   59.8%  (-2.0/+1.8)
 9    Qwen3-235B-A22B (Alibaba)                 58.4%  (-1.9/+2.1)
10    deepseek-r1 (DeepSeek)                    58.0%  (-2.2/+2.0)
11    o1-2024-12-17                             55.9%  (-2.2/+1.8)
12    gpt-4.5-preview (OpenAI)                  50.0%  (-1.9/+2.0)
13    o3-mini-2025-01-31                        50.0%  (baseline)
14    gpt-4.1 (OpenAI)                          50.0%  (-1.9/+1.7)
15    gpt-4.1-mini (OpenAI)                     46.9%  (-2.4/+2.1)
16    Qwen3-32B (Alibaba)                       44.5%  (-2.2/+2.1)
17    QwQ-32B (Alibaba)                         43.5%  (-2.5/+2.1)
18    Qwen3-30B-A3B (Alibaba)                   33.9%  (-1.6/+1.5)
19    claude-3-5-sonnet-20241022 (Anthropic)     33.0%  (-2.3/+1.8)
20    s1.1-32B                                  22.3%  (-1.7/+1.5)
21    llama4-maverick-instruct-basic (Meta)      17.2%  (-1.5/+1.2)
22    Athene-V2-Chat                            16.4%  (-1.4/+1.4)
23    gemma-3-27b-it (Google)                    15.0%  (-1.4/+1.0)
24    Qwen3-4B (Alibaba)                        15.0%  (-1.1/+1.5)
25    gpt-4.1-nano (OpenAI)                     13.7%  (-1.1/+1.0)

========================================================================
2. ARENA-HARD CREATIVE WRITING LEADERBOARD
   Ensemble Judges (GPT-4.1 + Gemini-2.5)
========================================================================

Rank  Model                     Score   95% CI
----  ------------------------  -----  -----------
 1    gemini-2.5 (Google)       90.8%  (-1.2/+1.3)
 2    o3-2025-04-16 (OpenAI)    88.8%  (-1.1/+1.0)
 3    gemini-2.5-flash (Google) 83.9%  (-1.3/+1.4)
 4    deepseek-r1 (DeepSeek)    77.0%  (-2.0/+1.4)
 5    Qwen3-235B-A22B (Alibaba) 73.5%  (-1.8/+1.5)
 6    gemma-3-27b-it (Google)   69.9%  (-1.9/+1.7)

========================================================================
3. CODING BENCHMARKS - SWE-bench & SWE-agent
========================================================================

SWE-bench Verified (500 human-verified GitHub issues):
- Leading agents use Claude Sonnet 4, GPT-4o, o3/o4-mini as backbones
- SWE-agent supports: claude-sonnet-4-20250514, o3, o4-mini, 
  gemini-2.5-pro, gemini-2.5-flash
- Multimodal SWE-bench: Claude Sonnet 4, o3, o4-mini, Gemini 2.5

Top SWE-bench Verified Resolution Rates (approximate):

Agent/Model                                  SWE-bench Verified %
--------------------------------------------  -------------------
OpenAI Codex (o3-based)                      ~70%+
Anthropic Claude Code (Sonnet 4)             ~65-72%
Google Jules (Gemini 2.5 Pro)                ~60-65%
Devin (Cognition AI)                         ~55-60%
Amazon Q Developer                           ~50-55%
SWE-agent + Claude Sonnet 4                  ~45-50%
SWE-agent + GPT-4o                           ~35-40%

========================================================================
4. AIDER CODE EDITING LEADERBOARD
========================================================================

Aider measures LLM ability to correctly edit code in pair-programming.
Key models supported with diff edit format:
- gpt-4o-2024-08-06
- claude-3-5-haiku-20241022
- deepseek-chat
- gemini-1.5-pro-exp (diff-fenced)

Top performers historically:
  1. Claude 3.5 Sonnet / Claude Sonnet 4 (best overall editing)
  2. GPT-4o / GPT-4.1 (strong editing)
  3. DeepSeek V3/R1 (competitive, much cheaper)
  4. Gemini 2.5 Pro (strong, especially with large context)
  5. Qwen3-235B (competitive open-weight)

========================================================================
5. RECENT MODEL RELEASES (as of May 2026)
========================================================================

OPENAI:
  - o3 (2025-04-16): Top reasoning model, 85.9% Arena-Hard
  - o4-mini (2025-04-16): Strong reasoning, smaller, 74.6-79.1%
  - o3-mini (2025-01-31): Mid-tier reasoning model
  - gpt-4.5-preview: Broad knowledge, 50.0% Arena-Hard
  - gpt-4.1 / gpt-4.1-mini / gpt-4.1-nano: New generation

ANTHROPIC:
  - Claude 3.7 Sonnet (thinking-16k): 59.8% Arena-Hard
  - Claude Sonnet 4 (claude-sonnet-4-20250514): Latest release
  - Claude 4: May exist beyond available data window

GOOGLE:
  - Gemini 2.5 Pro: 79.0% Arena-Hard (tied #2-3)
  - Gemini 2.5 Flash: 68.6% Arena-Hard
  - Gemma 3-27b-it: 15.0% Arena-Hard (open-weight)
  - Gemini 2.5 excels at creative writing (90.8%)

DEEPSEEK:
  - DeepSeek-R1: 58.0% Arena-Hard, 77.0% creative writing
  - DeepSeek-V3: Strong general model (referenced in benchmarks)

ALIBABA (QWEN):
  - Qwen3-235B-A22B: 58.4% Arena-Hard, 73.5% creative writing
  - Qwen3-32B: 44.5% Arena-Hard
  - QwQ-32B: 43.5% Arena-Hard (reasoning model)
  - Qwen3-30B-A3B: 33.9% Arena-Hard
  - Qwen3-4B: 15.0% Arena-Hard

META:
  - Llama 4 Maverick: 17.2% Arena-Hard (underwhelming)
  - Llama 3.3 Nemotron Super 49B: 88.7% (legacy v0.1 benchmark)

========================================================================
6. KEY TAKEAWAYS
========================================================================

TOP TIER (Reasoning/Chat):
  1. OpenAI o3 - Dominant on hard prompts (85.9%)
  2. OpenAI o4-mini / Google Gemini 2.5 - Tied at ~79%
  3. Gemini 2.5 Flash - Strong mid-tier (68.6%)
  4. Claude 3.7 Sonnet (thinking) - Competitive (59.8%)
  5. Qwen3-235B / DeepSeek-R1 - Best open-weight (~58%)

TOP TIER (Creative Writing):
  1. Gemini 2.5 Pro (90.8%) - Best creative writer
  2. OpenAI o3 (88.8%)
  3. Gemini 2.5 Flash (83.9%)
  4. DeepSeek-R1 (77.0%)

TOP TIER (Coding/Software Engineering):
  1. Claude Sonnet 4 / Claude Code - Best coding agent
  2. OpenAI o3 / Codex - Strong reasoning for code
  3. Gemini 2.5 Pro - Large context, good for big codebases
  4. DeepSeek R1/V3 - Cost-effective coding
  5. Qwen3-235B - Competitive open-weight

SURPRISING RESULTS:
  - Llama 4 Maverick scored only 17.2% on Arena-Hard (disappointing)
  - Gemini 2.5 dominates creative writing but trails o3 on hard prompts
  - Chinese models (Qwen3, DeepSeek) very competitive in open-weight

========================================================================
Data collected: May 23, 2026
Sources: LMArena/Arena-Hard-Auto GitHub, SWE-bench GitHub, 
         SWE-agent GitHub, Aider documentation, LiveCodeBench GitHub
========================================================================
