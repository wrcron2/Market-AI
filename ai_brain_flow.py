"""Generate MarketFlow AI Brain pipeline diagram."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe

# ── Canvas ────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(24, 14))
fig.patch.set_facecolor('#0d1117')
ax.set_facecolor('#0d1117')
ax.set_xlim(0, 24)
ax.set_ylim(0, 14)
ax.axis('off')

# ── Palette ───────────────────────────────────────────────────────────────────
C = {
    'bg':     '#0d1117',
    'card':   '#161b22',
    'border': '#30363d',
    'blue':   '#1e6bb8',
    'green':  '#238636',
    'purple': '#6e40c9',
    'orange': '#d29922',
    'gold':   '#e3b341',
    'teal':   '#0891b2',
    'red':    '#da3633',
    'text':   '#e6edf3',
    'muted':  '#8b949e',
    'white':  '#ffffff',
}

def box(ax, x, y, w, h, facecolor, edgecolor, alpha=1.0, radius=0.3):
    rect = FancyBboxPatch((x, y), w, h,
                          boxstyle=f"round,pad=0,rounding_size={radius}",
                          facecolor=facecolor, edgecolor=edgecolor,
                          linewidth=1.8, alpha=alpha, zorder=3)
    ax.add_patch(rect)

def label(ax, x, y, text, size=9, color='#e6edf3', weight='normal',
          ha='center', va='center', zorder=5):
    ax.text(x, y, text, fontsize=size, color=color, weight=weight,
            ha=ha, va=va, zorder=zorder,
            fontfamily='monospace')

def arrow(ax, x1, y1, x2, y2, color='#8b949e', lw=1.8, style='->', dashed=False):
    ls = (0, (5, 4)) if dashed else 'solid'
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=style, color=color,
                                lw=lw, linestyle=ls),
                zorder=4)

def badge(ax, x, y, text, bg, fg='#ffffff', size=7.5):
    ax.text(x, y, text, fontsize=size, color=fg, weight='bold',
            ha='center', va='center',
            bbox=dict(boxstyle='round,pad=0.22', facecolor=bg,
                      edgecolor='none', alpha=0.9),
            zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════════════════
ax.text(12, 13.5, 'MarketFlow AI  ·  Agent Pipeline',
        fontsize=17, color=C['white'], weight='bold',
        ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(12, 13.05, 'ai-brain/  ·  LangGraph StateGraph',
        fontsize=9, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# LLM ROUTER  (top-right legend)
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 19.3, 10.8, 4.4, 2.6, '#1a1f2e', C['purple'], radius=0.25)
ax.text(21.5, 13.1, '⚙  LLM Router', fontsize=9, color=C['purple'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)

box(ax, 19.5, 12.15, 3.95, 0.55, C['green']+'33', C['green'], radius=0.18)
ax.text(19.75, 12.42, '⚡', fontsize=9, color=C['green'], ha='left', va='center', zorder=6)
ax.text(20.15, 12.42, 'LOW → Ollama  qwen2.5-coder:7b',
        fontsize=7.5, color=C['green'], ha='left', va='center',
        fontfamily='monospace', zorder=6)

box(ax, 19.5, 11.5, 3.95, 0.55, C['purple']+'33', C['purple'], radius=0.18)
ax.text(19.75, 11.77, '☁', fontsize=9, color=C['purple'], ha='left', va='center', zorder=6)
ax.text(20.15, 11.77, 'HIGH → Bedrock  claude-3-5-sonnet',
        fontsize=7.5, color=C['purple'], ha='left', va='center',
        fontfamily='monospace', zorder=6)

ax.text(21.5, 11.1, 'agents/router.py  ·  Complexity enum',
        fontsize=7, color=C['muted'], ha='center', va='center',
        fontfamily='monospace', zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# AgentState sidebar
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 19.3, 7.0, 4.4, 3.55, '#1a1f2e', C['teal']+'88', radius=0.25)
ax.text(21.5, 10.3, 'AgentState  (TypedDict)',
        fontsize=8.5, color=C['teal'], weight='bold',
        ha='center', va='center', fontfamily='monospace', zorder=6)
for i, line in enumerate([
    'market_snapshot: dict',
    'signal: CandidateSignal | None',
    'debate_result: DebateResult | None',
    'risk_result: RiskAssessment | None',
    'submitted: bool',
]):
    ax.text(19.6, 9.95 - i * 0.52, line,
            fontsize=7.5, color=C['muted'],
            ha='left', va='center', fontfamily='monospace', zorder=6)
ax.text(21.5, 7.25, 'Shared across all graph nodes',
        fontsize=7, color=C['muted']+'bb',
        ha='center', va='center', fontfamily='monospace', zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# NODE 0 — DATA INPUT
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 0.4, 9.8, 4.0, 2.8, '#0e2038', C['blue'], radius=0.3)
ax.text(2.4, 12.38, '📊  DATA INPUT', fontsize=10, color=C['blue'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)

for i, (icon, txt) in enumerate([
    ('▸', 'Yahoo Finance  (yfinance, 5-min bars)'),
    ('▸', 'Demo Fallback  (random OHLCV)'),
    ('▸', 'IBKR Feed  (future)'),
]):
    ax.text(0.65, 11.95 - i * 0.52, icon, fontsize=8, color=C['blue'],
            ha='left', va='center', zorder=6)
    ax.text(0.95, 11.95 - i * 0.52, txt, fontsize=7.8, color=C['text'],
            ha='left', va='center', fontfamily='monospace', zorder=6)

ax.text(2.4, 10.5, 'symbol · OHLCV · RSI · MACD · BB · ATR',
        fontsize=7.2, color=C['muted'], ha='center', va='center',
        fontfamily='monospace', zorder=6)
ax.text(2.4, 10.08, 'VIX · SPY trend · sector flow',
        fontsize=7.2, color=C['muted'], ha='center', va='center',
        fontfamily='monospace', zorder=6)

badge(ax, 2.4, 9.95, 'data_feed/yahoo_feed.py', C['blue']+'99')

# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — SIGNAL AGENT
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 0.4, 6.3, 4.0, 3.1, '#0e2218', C['green'], radius=0.3)
ax.text(2.4, 9.17, '🤖  Signal Agent', fontsize=10, color=C['green'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
badge(ax, 1.15, 8.82, '⚡ Ollama / LOW', C['green'])
badge(ax, 3.15, 8.82, 'NODE 1', C['green']+'66', size=7)

ax.text(2.4, 8.42, 'Input:  market_snapshot', fontsize=7.5, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(2.4, 8.05, 'Output:  CandidateSignal', fontsize=7.8, color=C['text'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)

for i, txt in enumerate([
    'symbol · direction  (BUY/SELL/SHORT/COVER)',
    'quantity · limit_price',
    'strategy_name · initial_confidence  0–1',
    'reasoning',
]):
    ax.text(0.7, 7.65 - i * 0.36, f'  {txt}', fontsize=7.2, color=C['muted'],
            ha='left', va='center', fontfamily='monospace', zorder=6)

badge(ax, 2.4, 6.48, 'agents/signal_agent.py', C['green']+'99')

# ══════════════════════════════════════════════════════════════════════════════
# END — no signal
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 0.55, 4.8, 1.65, 0.65, '#2d0f0f', C['red'], radius=0.2)
ax.text(1.38, 5.13, '✕  END', fontsize=8.5, color=C['red'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(1.38, 4.6, 'no signal', fontsize=7, color=C['red']+'cc',
        ha='center', va='center', fontfamily='monospace', zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — DEBATE AGENT
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 5.2, 6.3, 5.6, 3.1, '#1a1030', C['purple'], radius=0.3)
ax.text(8.0, 9.17, '⚖  Debate Agent', fontsize=10, color=C['purple'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
badge(ax, 6.6, 8.82, '☁ Bedrock / HIGH', C['purple'])
badge(ax, 9.6, 8.82, 'NODE 2', C['purple']+'66', size=7)

# Bull sub-box
box(ax, 5.35, 7.55, 1.55, 1.0, '#0e2218', C['green']+'99', radius=0.18)
ax.text(6.12, 8.2, '🐂 Bull', fontsize=8, color=C['green'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(6.12, 7.88, 'Argues FOR', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(6.12, 7.68, 'the trade', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)

# Bear sub-box
box(ax, 7.05, 7.55, 1.55, 1.0, '#2d0f0f', C['red']+'99', radius=0.18)
ax.text(7.82, 8.2, '🐻 Bear', fontsize=8, color='#f85149',
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(7.82, 7.88, 'Argues AGAINST', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(7.82, 7.68, 'the trade', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)

# Judge sub-box
box(ax, 8.75, 7.55, 1.9, 1.0, '#1a1a0e', C['gold']+'99', radius=0.18)
ax.text(9.7, 8.2, '⚖ Judge', fontsize=8, color=C['gold'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(9.7, 7.88, 'Synthesizes', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(9.7, 7.68, 'both views', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)

ax.text(8.0, 7.32, 'Output:  DebateResult', fontsize=7.8, color=C['text'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(8.0, 6.98, 'adjusted_confidence · consensus_direction · bull/bear/judge reasoning',
        fontsize=7, color=C['muted'], ha='center', va='center',
        fontfamily='monospace', zorder=6)
badge(ax, 8.0, 6.48, 'agents/debate_agent.py', C['purple']+'99')

# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — RISK AGENT
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 11.6, 6.3, 4.3, 3.1, '#1e1800', C['orange'], radius=0.3)
ax.text(13.75, 9.17, '🛡  Risk Agent', fontsize=10, color=C['orange'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
badge(ax, 12.5, 8.82, '⚡ Ollama / LOW', C['green'])
badge(ax, 14.85, 8.82, 'NODE 3', C['orange']+'66', size=7)

ax.text(13.75, 8.45, 'Input:  CandidateSignal + DebateResult', fontsize=7.5,
        color=C['muted'], ha='center', va='center', fontfamily='monospace', zorder=6)

for i, txt in enumerate([
    '▸ position size vs daily volume',
    '▸ volatility & execution risk',
    '▸ concentration & macro/event risk',
]):
    ax.text(11.85, 8.05 - i * 0.36, txt, fontsize=7.2, color=C['muted'],
            ha='left', va='center', fontfamily='monospace', zorder=6)

ax.text(13.75, 7.05, 'Output:  RiskAssessment', fontsize=7.8, color=C['text'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(13.75, 6.72, 'is_blocked · final_confidence · risk_score · adjusted_quantity',
        fontsize=7, color=C['muted'], ha='center', va='center',
        fontfamily='monospace', zorder=6)

# Hard block note
box(ax, 11.75, 6.36, 3.95, 0.32, C['red']+'22', C['red']+'66', radius=0.12)
ax.text(13.72, 6.52, '⛔  block if final_confidence < 90%',
        fontsize=7, color='#f85149', ha='center', va='center',
        fontfamily='monospace', zorder=6)

badge(ax, 13.75, 6.48, '', '#00000000')  # spacer

# ══════════════════════════════════════════════════════════════════════════════
# END — risk blocked
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 12.55, 4.8, 1.85, 0.65, '#2d0f0f', C['red'], radius=0.2)
ax.text(13.48, 5.13, '✕  END', fontsize=8.5, color=C['red'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(13.48, 4.6, 'risk blocked', fontsize=7, color=C['red']+'cc',
        ha='center', va='center', fontfamily='monospace', zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — SUBMIT
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 5.2, 3.2, 5.5, 2.65, '#0d1f2d', C['teal'], radius=0.3)
ax.text(7.95, 5.62, '📤  Submit Node', fontsize=10, color=C['teal'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
badge(ax, 7.95, 5.28, 'NODE 4', C['teal']+'66', size=7)

ax.text(7.95, 4.95, 'HTTP POST  →  /api/signals', fontsize=8, color=C['text'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(7.95, 4.62, 'signal_id · symbol · direction · quantity · limit_price',
        fontsize=7.2, color=C['muted'], ha='center', va='center',
        fontfamily='monospace', zorder=6)
ax.text(7.95, 4.3, 'confidence · full_reasoning · strategy_name · model_used',
        fontsize=7.2, color=C['muted'], ha='center', va='center',
        fontfamily='monospace', zorder=6)
badge(ax, 7.95, 3.38, 'agents/orchestrator.py  ·  _node_submit()', C['teal']+'99')

# ══════════════════════════════════════════════════════════════════════════════
# GO BACKEND
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 0.4, 1.5, 4.2, 1.9, '#051e22', C['teal'], radius=0.3)
ax.text(2.5, 3.18, '🔧  Go Backend', fontsize=10, color=C['teal'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(2.5, 2.78, 'Stages order → SQLite  (status: PENDING)', fontsize=7.8,
        color=C['text'], ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(2.5, 2.42, 'Pushes via WebSocket → React Dashboard', fontsize=7.8,
        color=C['text'], ha='center', va='center', fontfamily='monospace', zorder=6)
badge(ax, 2.5, 1.68, 'backend/  ·  :8080 REST  ·  :8081 WS  ·  :50051 gRPC', C['teal']+'88')

# ══════════════════════════════════════════════════════════════════════════════
# GREEN LIGHT GATE
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 5.4, 1.5, 5.3, 1.9, '#1e1700', C['gold'], radius=0.3, alpha=1.0)
ax.text(8.05, 3.18, '🟢  Green Light Gate', fontsize=10, color=C['gold'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(8.05, 2.78, 'Human reviews: symbol · direction · confidence · reasoning',
        fontsize=7.5, color=C['text'], ha='center', va='center',
        fontfamily='monospace', zorder=6)

# Green Light branch
box(ax, 5.55, 1.62, 2.0, 0.52, '#0e2218', C['green']+'99', radius=0.15)
ax.text(6.55, 1.88, '✅  Green Light  →  IBKR / Sim', fontsize=7.2,
        color=C['green'], ha='center', va='center', fontfamily='monospace', zorder=6)

# Reject branch
box(ax, 8.2, 1.62, 2.3, 0.52, '#2d0f0f', C['red']+'99', radius=0.15)
ax.text(9.35, 1.88, '❌  Reject  →  Discarded', fontsize=7.2,
        color='#f85149', ha='center', va='center', fontfamily='monospace', zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# IBKR / Sim
# ══════════════════════════════════════════════════════════════════════════════
box(ax, 11.6, 1.5, 3.9, 1.9, '#0c1c0c', C['green'], radius=0.3)
ax.text(13.55, 3.18, '⚡  Execution', fontsize=10, color=C['green'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)

box(ax, 11.75, 2.45, 1.6, 0.7, '#0e2218', C['green']+'88', radius=0.15)
ax.text(12.55, 2.82, 'IBKR API', fontsize=8, color=C['green'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(12.55, 2.55, 'Real order', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)

box(ax, 13.55, 2.45, 1.8, 0.7, '#1a1f2e', C['blue']+'88', radius=0.15)
ax.text(14.45, 2.82, 'Sim Executor', fontsize=8, color=C['blue'],
        weight='bold', ha='center', va='center', fontfamily='monospace', zorder=6)
ax.text(14.45, 2.55, 'Virtual $100k', fontsize=7, color=C['muted'],
        ha='center', va='center', fontfamily='monospace', zorder=6)

badge(ax, 13.55, 1.68, 'PAPER_TRADING=true  →  IB port 7497', C['green']+'88')

# ══════════════════════════════════════════════════════════════════════════════
# ARROWS
# ══════════════════════════════════════════════════════════════════════════════

# Data → Signal Agent
arrow(ax, 2.4, 9.8, 2.4, 9.4, color=C['blue'])

# Signal Agent → Debate Agent
arrow(ax, 4.4, 7.85, 5.2, 7.85, color=C['white'])

# Signal Agent → END (no signal)  dashed
arrow(ax, 2.4, 6.3, 2.4, 5.45, color=C['red'], dashed=True)

# Debate → Risk Agent
arrow(ax, 10.8, 7.85, 11.6, 7.85, color=C['white'])

# Risk → Submit
arrow(ax, 13.75, 6.3, 10.7, 4.85, color=C['white'])

# Risk → END (blocked)  dashed
arrow(ax, 13.75, 6.3, 13.75, 5.45, color=C['red'], dashed=True)

# Submit → Go Backend
arrow(ax, 7.0, 3.2, 4.6, 3.18, color=C['teal'])

# Go Backend → Green Light
arrow(ax, 4.6, 2.4, 5.4, 2.4, color=C['gold'])

# Green Light → Execution
arrow(ax, 10.7, 2.4, 11.6, 2.4, color=C['green'])

# Arrow labels
ax.text(4.8, 8.0, 'CandidateSignal', fontsize=7, color=C['muted'],
        ha='center', va='bottom', fontfamily='monospace', zorder=6)
ax.text(11.2, 8.0, 'CandidateSignal\n+ DebateResult', fontsize=7, color=C['muted'],
        ha='center', va='bottom', fontfamily='monospace', zorder=6)
ax.text(12.2, 4.35, 'approved', fontsize=7, color=C['white'],
        ha='center', va='bottom', fontfamily='monospace', zorder=6)
ax.text(6.6, 3.3, 'POST /api/signals', fontsize=7, color=C['teal'],
        ha='center', va='bottom', fontfamily='monospace', zorder=6)
ax.text(5.0, 2.55, 'PENDING', fontsize=7, color=C['gold'],
        ha='center', va='bottom', fontfamily='monospace', zorder=6)
ax.text(11.15, 2.55, 'IBKR / Sim', fontsize=7, color=C['green'],
        ha='center', va='bottom', fontfamily='monospace', zorder=6)

# ══════════════════════════════════════════════════════════════════════════════
# LANGGRAPH label
# ══════════════════════════════════════════════════════════════════════════════
ax.text(0.5, 0.35, 'Built with  LangGraph  StateGraph  ·  agents/orchestrator.py',
        fontsize=8, color=C['muted'], ha='left', va='center',
        fontfamily='monospace', zorder=6)
ax.text(23.5, 0.35, 'MarketFlow AI  ·  ai-brain/',
        fontsize=8, color=C['muted'], ha='right', va='center',
        fontfamily='monospace', zorder=6)

# thin bottom rule
ax.axhline(0.6, color=C['border'], lw=0.8, zorder=3)

# ── Save ───────────────────────────────────────────────────────────────────────
out = '/Users/ronleibovitch/Documents/Claude/Projects/Market-AI/ai_brain_pipeline.png'
plt.savefig(out, dpi=150, bbox_inches='tight',
            facecolor=fig.get_facecolor(), edgecolor='none')
print(f'Saved: {out}')
