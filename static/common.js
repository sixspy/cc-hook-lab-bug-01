const 投递台 = {
  async getJSON(url, opt) {
    const r = await fetch(url, opt);
    if (r.status === 401) { location.href = '/login'; throw new Error('未登录'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.错误 || data.error || r.statusText);
    return data;
  },
  async postJSON(url, body) {
    return this.getJSON(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  },
  安全文本(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  },
  徽章(状态) {
    const span = document.createElement('span');
    span.className = '徽章 ' + (状态 || '');
    span.textContent = 状态 || '—';
    return span;
  },
  账实徽章(row) {
    const state = row?.账实标记 || (row?.合法入账 ? '独立凭证合法入队' : '缺过滤凭证');
    const span = document.createElement('span');
    const bad = state.includes('短路') || state.includes('缺') || row?.合法入账 === false;
    span.className = '徽章 ' + (bad ? '脏账' : '合法');
    span.textContent = state;
    span.title = row?.过滤凭证摘要?.指纹 || row?.过滤指纹 || '';
    return span;
  },
  行(html) {
    const tr = document.createElement('tr');
    tr.innerHTML = html;
    return tr;
  },
  空态(msg) { return msg || '暂无数据'; },
  async 刷新过滤芯片() {
    const el = document.getElementById('filt-chip');
    if (!el) return null;
    try {
      const d = await this.getJSON('/api/filt/看板');
      const fp = d.cutover_fp ? String(d.cutover_fp).slice(0, 18) : '无指纹';
      el.classList.remove('ok', 'bad', 'warn');
      if (d.aligned) {
        el.classList.add('ok');
        el.textContent = `过滤器凭证已对齐 · ${d.active_read} · ${fp}`;
      } else {
        el.classList.add('bad');
        el.textContent = `过滤器账实异常 · 页绿库脏=${d.panel_pack_mismatch ? '是' : '否'} · ${fp}`;
      }
      el.title = JSON.stringify({
        bone: d.bone,
        truth: d.truth,
        reject_refresh: d.reject_refresh,
        gate_bypass: d.gate_bypass,
        selected_form: d.selected_form,
      }, null, 2);
      return d;
    } catch (e) {
      el.classList.remove('ok');
      el.classList.add('warn');
      el.textContent = '过滤器口径未读取：' + e.message;
      return null;
    }
  },
};

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => 投递台.刷新过滤芯片());
}
