/* Account forms use no localStorage; invitation/recovery tokens stay in memory. */
(() => {
  const el = id => document.getElementById(id);
  const message = (text, error = false) => {
    const box = el('account-message'); box.textContent = text; box.dataset.error = String(error);
    if (error) box.focus();
  };
  const csrf = () => document.cookie.split('; ').find(s => s.startsWith('stocks_csrf='))?.slice(12) || '';
  async function request(path, method = 'POST', body = {}) {
    const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf()}, ...(method === 'GET' ? {} : {body: JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '欄位格式不正確，請檢查輸入');
    return data;
  }
  function form(id, submit) {
    el(id).addEventListener('submit', async event => {
      event.preventDefault(); const button = event.currentTarget.querySelector('button[type=submit]');
      const original = button.textContent; button.disabled = true; button.textContent = '處理中…'; message('');
      try { await submit(); } catch (error) { message(error.message, true); }
      finally { button.disabled = false; button.textContent = original; }
    });
  }
  function newPassword() {
    if (el('new-password').value !== el('confirm-password').value) throw new Error('兩次輸入的密碼不一致');
    return el('new-password').value;
  }
  document.querySelectorAll('input[type="password"]').forEach(input => {
    const wrap = document.createElement('div'); wrap.className = 'password-control';
    input.before(wrap); wrap.append(input);
    const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'ghost small password-toggle';
    const label = document.querySelector(`label[for="${input.id}"]`)?.textContent || '密碼';
    toggle.textContent = '顯示'; toggle.setAttribute('aria-label', `顯示${label}`); toggle.setAttribute('aria-controls', input.id); toggle.setAttribute('aria-pressed','false');
    toggle.onclick = () => { const show = input.type === 'password'; input.type = show ? 'text' : 'password'; toggle.textContent = show ? '隱藏' : '顯示'; toggle.setAttribute('aria-label', `${show ? '隱藏' : '顯示'}${label}`); toggle.setAttribute('aria-pressed',String(show)); };
    wrap.append(toggle);
  });
  if (document.body.dataset.accountPage === 'login') {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const token = fragment.get('invite') || fragment.get('reset');
    if (location.hash) history.replaceState(null, '', location.pathname);
    if (token) {
      el('login-form').hidden = true; el('complete-form').hidden = false;
      el('form-title').textContent = fragment.has('invite') ? '啟用你的工作區' : '重設登入密碼';
      el('form-description').textContent = '完成後請使用新密碼登入。';
      el('new-password').focus();
    }
    form('login-form', async () => {
      await request('/api/auth/login', 'POST', {username: el('username').value, password: el('password').value});
      location.replace('/index.html');
    });
    form('complete-form', async () => {
      const password = newPassword();
      try { await request('/api/auth/complete', 'POST', {token, password}); }
      catch (error) { el('token-recovery').hidden = false; throw error; }
      el('token-recovery').hidden = true; el('complete-form').reset(); el('complete-form').hidden = true; el('login-form').hidden = false;
      el('form-title').textContent = '密碼已設定'; el('form-description').textContent = '請使用你的帳號與新密碼登入。';
      el('username').focus();
    });
    return;
  }
  async function showLink(data) {
    el('issued-link').hidden = false; el('token-link').value = new URL(data.link, location.origin).href;
    el('token-expiry').textContent = '有效期限：' + new Date(data.expires_at * 1000).toLocaleString('zh-TW');
    el('token-link').focus(); el('token-link').select();
  }
  async function loadMembers() {
    const rows = await request('/api/admin/accounts', 'GET');
    const body = el('members-body'); body.replaceChildren();
    rows.forEach(user => {
      const tr = document.createElement('tr');
      const cell = (...children) => { const td = document.createElement('td'); td.append(...children); tr.append(td); };
      const span = (className, text, state) => { const node = document.createElement('span'); node.className = className; node.textContent = text; if (state) node.dataset.state = state; return node; };
      cell(span('member-name', user.display_name), span('member-username', user.username));
      cell(user.role === 'admin' ? '管理員' : '成員');
      const [state, label] = user.active ? ['active', '已啟用'] : user.configured ? ['disabled', '已停用'] : ['pending', '待設定密碼'];
      cell(span('member-state', label, state));
      const td = document.createElement('td'); const actions = document.createElement('div'); actions.className = 'account-actions';
      function action(label, method, suffix, payload, confirmation) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'ghost small'; button.textContent = label; button.setAttribute('aria-label', `${label}：${user.display_name} / ${user.username}`);
        button.addEventListener('click', async () => {
          if (confirmation && !confirm(confirmation)) return;
          button.disabled = true; message('');
          try { const result = await request(`/api/admin/accounts/${user.id}${suffix}`, method, payload || {}); if (result.link) await showLink(result); else { el('issued-link').hidden = true; el('token-link').value = ''; } await loadMembers(); message(`${user.display_name}：${label}完成`); }
          catch (error) { message(error.message, true); } finally { button.disabled = false; }
        }); actions.append(button);
      }
      if (!user.configured) action('重發邀請', 'POST', '/invite');
      if (user.active) action('重設密碼', 'POST', '/reset');
      action('撤銷連結', 'DELETE', '/tokens');
      if (user.id !== 1 && user.configured) action(user.active ? '停用' : '啟用', 'PATCH', '', {active: !user.active}, user.active ? `停用「${user.display_name}」並登出所有裝置？` : null);
      td.append(actions); tr.append(td); body.append(tr);
    });
  }
  el('logout-button').addEventListener('click', async () => { try { await request('/api/auth/logout'); location.replace('/login.html'); } catch (error) { message(error.message, true); } });
  form('change-password-form', async () => { await request('/api/auth/password', 'POST', {current_password: el('current-password').value, new_password: newPassword()}); location.replace('/login.html'); });
  form('invite-form', async () => { await showLink(await request('/api/admin/accounts/invite', 'POST', {username: el('invite-username').value, display_name: el('invite-name').value})); el('invite-form').reset(); await loadMembers(); message('邀請已建立，請複製連結並私下傳送。'); });
  el('copy-link').addEventListener('click', async () => { try { await navigator.clipboard.writeText(el('token-link').value); message('連結已複製'); } catch { el('token-link').focus(); el('token-link').select(); message('請手動複製選取的連結'); } });
  window.accountReady.then(async user => {
    el('profile-name').textContent = user.display_name;
    el('profile-username').textContent = user.username || '—';
    el('profile-role').textContent = user.role === 'admin' ? '管理員' : '成員';
    if (user.auth_mode === 'basic') { document.querySelector('.account-password-section').hidden = true; el('logout-button').hidden = true; message('目前為舊版單人模式。請先依開發文件啟用帳號系統。'); return; }
    el('account-description').textContent = user.role === 'admin' ? '管理自己的登入方式，以及成員邀請與存取權限。' : '管理自己的登入方式；邀請與帳號恢復請聯絡管理員。';
    if (user.role === 'admin') { el('member-management').hidden = false; await loadMembers(); }
  }).catch(error => message(error.message, true));
})();
