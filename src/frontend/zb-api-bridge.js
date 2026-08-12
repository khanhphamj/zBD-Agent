(function () {
  'use strict';

  var API = '/api/v1';
  var TOKEN_KEY = 'zb.token';
  var REFRESH_KEY = 'zb.refresh';

  var ZB = {
    ready: false,
    me: null,
    customers: null,
    proposals: null,
    kbs: null,
    skills: null,
    tpls: null,
    dir: null,
    reqs: null,
    users: null,
    prods: null,
    notifs: null,
    notifWeek: null,
    topProds: null,
    lastError: null
  };
  window.__ZB = ZB;

  var listeners = [];

  ZB.subscribe = function (fn) {
    if (typeof fn !== 'function') return function () {};
    listeners.push(fn);
    if (ZB.ready) fn();
    return function () {
      var i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    };
  };

  function emit() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](); } catch (e) {}
    }
  }

  var ICONS = ['102', '103', '104', '105', '108', '17', '18', '27', '33', '34', '35', '36', '38', '46', '47'];

  function token() {
    try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }
  function setToken(t, r) {
    try {
      if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY);
      if (r) localStorage.setItem(REFRESH_KEY, r);
    } catch (e) {}
  }

  function req(path, opts) {
    opts = opts || {};
    var headers = { 'Content-Type': 'application/json' };
    var t = token();
    if (t) headers.Authorization = 'Bearer ' + t;
    return fetch(API + path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.text().then(function (txt) {
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var err = new Error((data && data.error && data.error.message) || ('HTTP ' + r.status));
          err.code = (data && data.error && data.error.code) || ('HTTP_' + r.status);
          err.payload = data && data.error;
          throw err;
        }
        return data;
      });
    });
  }

  function ago(iso) {
    if (!iso) return '';
    var then = new Date(iso).getTime();
    if (isNaN(then)) return '';
    var s = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (s < 60) return 'vừa xong';
    var m = Math.floor(s / 60);
    if (m < 60) return m + ' phút trước';
    var h = Math.floor(m / 60);
    if (h < 24) return h + ' giờ trước';
    var d = Math.floor(h / 24);
    if (d === 1) return 'Hôm qua';
    if (d < 7) return d + ' ngày trước';
    var w = Math.floor(d / 7);
    if (w < 5) return w + ' tuần trước';
    var mo = Math.floor(d / 30);
    return mo + ' tháng trước';
  }

  function initials(name) {
    var parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '??';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function ownerOf(o) {
    if (!o) return 0;
    if (ZB.me && o.id && o.id === ZB.me.id) return 0;
    var handle = o.handle || o.name || '';
    if (ZB.me && handle === ZB.me.handle) return 0;
    return handle || 0;
  }

  var SKILL_ICON = '105';

  var TOOL_LABELS = {
    web_search: 'Web Search',
    crawler: 'Crawler',
    product_kb: 'Product Knowledge Hub',
    template_library: 'Template Library'
  };

  var TOOL_SHORT = {
    web_search: 'Web Search',
    crawler: 'Crawler',
    product_kb: 'Product KB',
    template_library: 'Template'
  };

  ZB.TOOL_LABELS = TOOL_LABELS;

  // Tag trên card skill lấy từ Công cụ & nguồn dữ liệu, không phải stage.
  function toolTag(tools, stage) {
    var list = (tools || []).filter(function (t) { return TOOL_SHORT[t]; });
    if (!list.length) return stage || 'Chưa gán công cụ';
    if (list.length > 2) return list.length + ' công cụ';
    return list.map(function (t) { return TOOL_SHORT[t]; }).join(' · ');
  }

  function iconFor(i) {
    return ICONS[i % ICONS.length];
  }

  // ── Backend shape -> prototype shape ─────────────────────────────────────

  function mapCustomers(page) {
    return (page.items || []).map(function (c, i) {
      return {
        key: c.id,
        prio: 0,
        name: c.name,
        short: c.shortName || c.name,
        web: c.website || '',
        init: initials(c.shortName || c.name),
        industry: c.industry || '',
        status: c.status,
        tone: i % 5,
        owner: ownerOf(c.owner),
        updated: ago(c.updatedAt),
        __id: c.id
      };
    });
  }

  function mapProposals(page, tplName) {
    return (page.items || []).map(function (p) {
      var ver = p.version || 'v1';
      var when = ago(p.updatedAt);
      return {
        title: p.title,
        cust: (p.customer && p.customer.id) || '',
        tpls: [tplName(p.templateId) || 'Proposal tiêu chuẩn'],
        skills: [],
        kbs: [],
        status: p.status === 'Hoàn tất' ? 'Hoàn tất' : 'Draft',
        ver: ver,
        updated: when,
        dash: ver + ' · ' + (p.status === 'Hoàn tất' ? 'hoàn tất ' : 'sửa ') + when,
        prods: [],
        __id: p.id
      };
    });
  }

  function mapKbs(res) {
    return (res.items || []).map(function (k, i) {
      return {
        name: k.name,
        scope: k.scope,
        owner: k.ownerId && ZB.me && k.ownerId === ZB.me.id ? 0 : (k.owner || 'Admin'),
        icon: iconFor(i),
        tags: k.tags || [],
        desc: k.description || '',
        docs: k.docCount || 0,
        updated: ago(k.updatedAt),
        sources: k.sources || [],
        files: [],
        locked: !!k.locked,
        __id: k.id
      };
    });
  }

  function mapSkills(res) {
    return (res.items || []).map(function (s, i) {
      return {
        name: s.name,
        scope: s.scope,
        owner: ownerOf(s.owner) || 'Admin',
        icon: SKILL_ICON,
        ver: s.version || 'v1.0',
        runs: 0,
        desc: s.description || '',
        // stage phải là GIÁ TRỊ THẬT: wizard Tạo proposal lọc stage === 'Proposal',
        // picker research lọc === 'Research'. Nhãn công cụ (trang trí chip) tách
        // sang `tag` — trước đây đè vào stage làm skill Proposal biến mất khỏi wizard.
        stage: s.stage || 'Research',
        tag: toolTag(s.tools, s.stage),
        tools: s.tools || [],
        prompt: s.prompt || '',
        model: s.model || null,
        temperature: s.temperature,
        inputSchema: s.inputSchema || [],
        updated: ago(s.updatedAt),
        updatedIso: s.updatedAt || null,
        __id: s.id
      };
    });
  }

  var coverCache = {};

  // Route thumbnail cần Bearer token, mà CSS url() không gửi header được (401).
  // Nên tải bằng fetch có auth rồi đổi sang blob URL — đúng cách prototype vẫn dùng.
  function loadCovers(items) {
    var jobs = items
      .filter(function (t) { return t.coverUrl && !coverCache[t.coverUrl]; })
      .map(function (t) {
        var headers = {};
        var tok = token();
        if (tok) headers.Authorization = 'Bearer ' + tok;
        return fetch(t.coverUrl, { headers: headers })
          .then(function (r) { return r.ok ? r.blob() : null; })
          .then(function (b) { if (b) coverCache[t.coverUrl] = URL.createObjectURL(b); })
          .catch(function () {});
      });
    if (!jobs.length) return Promise.resolve();
    return Promise.all(jobs).then(function () { emit(); });
  }

  function mapTpls(res) {
    var covers = [
      'linear-gradient(120deg,#1A30A0,#0068FF)',
      'linear-gradient(120deg,#0068FF,#787CA6)',
      'linear-gradient(120deg,#021F4E,#1A30A0)',
      'linear-gradient(120deg,#1A30A0,#37DEE7)',
      'linear-gradient(120deg,#787CA6,#0068FF)'
    ];
    return (res.items || []).map(function (t, i) {
      var th = t.theme || {};
      var blob = t.coverUrl ? coverCache[t.coverUrl] : null;
      // Ảnh bìa thật nằm dưới một lớp scrim, vì prototype vẽ tên + meta đè lên bìa;
      // không có scrim thì chữ trắng chìm vào ảnh sáng.
      var cover = blob
        ? 'linear-gradient(180deg,rgba(2,31,78,.12),rgba(2,31,78,.78)),'
          + 'center/cover no-repeat url(' + blob + ')'
        : covers[i % covers.length];
      return {
        name: t.name,
        scope: t.scope,
        slides: t.slideCount || 0,
        lang: th.language || 'VI',
        ratio: th.ratio || null,
        ratioExact: th.ratioExact ? parseFloat(th.ratioExact) : null,
        fonts: th.fonts || [],
        palette: th.palette || [],
        pageSize: th.pageSize || null,
        kind: th.kind || null,
        ver: t.version || 'v1.0',
        owner: ownerOf(t.owner) || 'Admin',
        updated: ago(t.updatedAt),
        sub: t.subtitle || '',
        cover: cover,
        coverUrl: t.coverUrl || null,
        __id: t.id
      };
    });
  }

  function mapDir(res) {
    var colors = ['#1A30A0', '#0068FF', '#787CA6'];
    return (res.items || [])
      .filter(function (d) { return d.type === 'user'; })
      .map(function (d, i) {
        var team = d.team || 'BD';
        return [d.handle, team, colors[i % colors.length]];
      });
  }

  var REQ_KIND = {
    resource_access: 'access',
    publish_shared: 'publish',
    role_upgrade: 'role',
    company_link: 'link',
    account: 'access'
  };

  var PERM_LABEL = {
    view: 'Chỉ xem',
    comment: 'Bình luận',
    edit: 'Chỉnh sửa',
    manage: 'Quản lý'
  };

  var GO_FOR = {
    proposal: 'proposals',
    customer: 'customers',
    template: 'templates',
    knowledge_base: 'skills',
    skill: 'skills'
  };

  function mapReqs(res) {
    var colors = ['#0068FF', '#1A30A0', '#787CA6', '#12A150'];
    return (res.items || [])
      .filter(function (r) { return r.status === 'pending'; })
      .map(function (r, i) {
        var rq = r.requester || {};
        var who = rq.handle || rq.name || rq.email || '';
        var label = (r.target && r.target.label) || '';
        var kind = REQ_KIND[r.type] || 'access';
        var perm = PERM_LABEL[r.permission] || r.permission || '';
        var act, ok, okMsg;

        if (kind === 'publish') {
          act = 'đề nghị publish dùng chung';
          ok = 'Duyệt & publish';
          okMsg = 'Đã publish ' + label + ' cho cả đội';
        } else if (kind === 'role') {
          act = 'xin nâng quyền ' + (r.requestedRole || '');
          ok = 'Duyệt & nâng quyền';
          okMsg = 'Đã nâng quyền cho ' + who;
        } else if (kind === 'link') {
          act = 'xin liên kết công ty';
          ok = 'Duyệt liên kết';
          okMsg = 'Đã liên kết ' + label;
        } else {
          act = 'xin quyền ' + perm;
          ok = 'Duyệt & cấp ' + perm;
          okMsg = 'Đã cấp quyền ' + perm + ' cho ' + who + ' trên ' + label;
        }

        return {
          who: who,
          c: rq.color || colors[i % colors.length],
          type: kind,
          act: act,
          obj: label,
          reason: r.reason || '',
          time: ago(r.createdAt),
          ok: ok,
          okMsg: okMsg,
          openLabel: 'Mở ' + (label.split('·')[0] || 'tài nguyên').trim(),
          go: GO_FOR[(r.target && r.target.resourceType) || ''] || 'dashboard',
          __id: r.id
        };
      });
  }

  function mapUsers(res) {
    return (res.items || []).map(function (u) {
      return {
        handle: u.handle,
        email: u.email,
        init: u.initials,
        c: u.avatarColor,
        role: u.role === 'ADMIN' ? 'Admin' : 'Member',
        team: u.team || '—',
        last: u.lastLoginAt ? ago(u.lastLoginAt) : 'Chưa đăng nhập',
        locked: u.status === 'locked',
        __id: u.id
      };
    });
  }

  function iconStyleFor(i, iconUrl) {
    var url = iconUrl;
    if (!url) {
      var key = 'pptx/media/image' + iconFor(i) + '.png';
      var res = window.__resources;
      url = (res && res[key]) || '/' + key;
    }
    return 'width:22px;height:22px;flex:none;background:url(' + url +
      ') center/contain no-repeat';
  }

  function mapProds(res) {
    return (res.items || []).map(function (p, i) {
      var limits = (p.pricing || []).map(function (row) {
        if (row && (row.label || row.value)) return [row.label || '', String(row.value || '')];
        if (row && (row.name || row.price)) return [row.name || '', String(row.price || '')];
        return ['', typeof row === 'string' ? row : JSON.stringify(row)];
      });
      if (!limits.length) {
        if ((p.packages || []).length) limits.push(['Gói áp dụng', p.packages.join(' · ')]);
        if ((p.targetIndustries || []).length)
          limits.push(['Ngành mục tiêu', p.targetIndustries.join(' · ')]);
      }

      var sources = (p.documents || [])
        .map(function (d) { return d && (d.url || d.href || d.link); })
        .filter(Boolean)
        .map(function (u, k) { return [String(u).replace(/^https?:\/\//, ''), k]; });
      if (!sources.length && p.sourceUrl)
        sources.push([String(p.sourceUrl).replace(/^https?:\/\//, ''), 0]);

      var priceRow = (p.pricing || []).find(function (r) { return r && (r.price || r.value); });

      // Dịch vụ con — đơn vị matching; PKH card hiện accordion từ danh sách này.
      var services = (p.services || []).map(function (s) {
        var priceTxt = (s.pricing || [])
          .map(function (r) { return r && r.price; })
          .filter(Boolean)
          .join(' · ');
        var reqTxt = (s.requires || [])
          .map(function (r) { return r && r.note; })
          .filter(Boolean)
          .join(' • ');
        return {
          id: s.id,
          name: s.name,
          solves: s.solves || s.summary || '',
          price: priceTxt,
          pm: s.pricingModel || '',
          grp: s.group || '',
          req: reqTxt
        };
      });
      var prodReq = (p.requires || [])
        .map(function (r) { return r && r.note; })
        .filter(Boolean)
        .join(' • ');

      return {
        name: p.name,
        family: p.family || 'Khác',
        group: p.group || '',
        // mô tả gọn cho card — chi tiết nằm ở section dịch vụ con
        desc: (function (d) {
          d = d || '';
          return d.length > 180 ? d.slice(0, 178).replace(/\s+\S*$/, '') + '…' : d;
        })(p.description),
        // tối đa 3 chip — tường chip cũ gây nhiễu, dịch vụ con nói thay
        tags: ((p.capabilities || []).length ? p.capabilities : (p.targetIndustries || [])).slice(0, 3),
        pkgs: p.packages || [],
        limits: limits,
        tech: p.positioning || '',
        price: priceRow ? String(priceRow.price || priceRow.value) : '',
        iconStyle: iconStyleFor(i, p.iconUrl),
        sources: sources,
        services: services,
        req: prodReq,
        pm: p.pricingModel || '',
        __id: p.id
      };
    });
  }

  var NOTIF_META = {
    research_done: ['Research', 1, '74'],
    handover: ['Research', 1, '76'],
    proposal_done: ['Proposal', 2, '65'],
    approval_request: ['Proposal', 2, '107'],
    approval_result: ['Proposal', 2, '107'],
    comment: ['Bình luận', 4, '102'],
    share: ['Chia sẻ', 3, '136'],
    access_request: ['Chia sẻ', 3, '104'],
    invite: ['Chia sẻ', 3, '108']
  };

  var NOTIF_FALLBACK = ['Chia sẻ', 3, '149'];

  var URL_SCREEN = {
    customers: 'customer',
    proposals: 'proposal',
    knowledge: 'skills',
    skills: 'skills',
    templates: 'templates'
  };

  function routeOf(url) {
    var m = String(url || '').match(/^\/([a-z-]+)(?:\/([^/?#]+))?/);
    var screen = m && URL_SCREEN[m[1]];
    if (!screen) return { screen: 'dashboard', id: '' };
    return { screen: screen, id: (m && m[2]) || '' };
  }

  function mapNotifs(res) {
    return (res.items || []).map(function (n) {
      var meta = NOTIF_META[n.type] || NOTIF_FALLBACK;
      var route = routeOf(n.targetUrl);
      return {
        id: n.id,
        title: n.title,
        body: n.body || '',
        time: ago(n.createdAt),
        cat: meta[0],
        f: meta[1],
        icon: meta[2],
        read: !!n.read,
        screen: route.screen,
        refId: route.id,
        __id: n.id
      };
    });
  }

  function mapNotifWeek(res) {
    var w = res.weekSummary || {};
    return {
      research: w.research || 0,
      proposal: w.proposal || 0,
      share: w.share || 0,
      comment: w.comment || 0,
      total: w.total || 0
    };
  }

  function mapTopProds(res) {
    var colors = ['#0068FF', '#1A30A0', '#787CA6'];
    return (res.topProducts || []).map(function (t, i) {
      return {
        id: t.id,
        name: t.name,
        pct: t.pct,
        count: t.customerCount,
        barStyle: 'width:' + t.pct + '%;height:6px;background:' + colors[i % colors.length] +
          ';border-radius:4px;animation:zbBar .9s ' + (0.3 + i * 0.15) +
          's cubic-bezier(.22,1,.36,1) both'
      };
    });
  }

  // ── Loading ──────────────────────────────────────────────────────────────

  function soft(path, fallback) {
    return req(path).catch(function () { return fallback; });
  }

  function loadAll() {
    return req('/me')
      .then(function (me) {
        ZB.me = me;
        return Promise.all([
          req('/customers?size=100'),
          req('/proposals?size=100'),
          req('/knowledge-bases'),
          req('/skills?detail=true'),
          req('/templates'),
          req('/directory?limit=50&includeTeams=false'),
          soft('/access-requests', { items: [] }),
          soft('/admin/users', { items: [] }),
          req('/products?detail=true&status=active'),
          req('/notifications?limit=50'),
          req('/dashboard')
        ]);
      })
      .then(function (r) {
        var tplRes = r[4];
        var byId = {};
        (tplRes.items || []).forEach(function (t) { byId[t.id] = t.name; });

        ZB.customers = mapCustomers(r[0]);
        ZB.proposals = mapProposals(r[1], function (id) { return byId[id]; });
        ZB.kbs = mapKbs(r[2]);
        ZB.skills = mapSkills(r[3]);
        ZB.tpls = mapTpls(tplRes);
        loadCovers(tplRes.items || []);
        ZB.dir = mapDir(r[5]);
        ZB.reqs = mapReqs(r[6]);
        ZB.users = mapUsers(r[7]);
        ZB.prods = mapProds(r[8]);
        ZB.notifs = mapNotifs(r[9]);
        ZB.notifWeek = mapNotifWeek(r[9]);
        ZB.topProds = mapTopProds(r[10]);
        ZB.ready = true;
        ZB.lastError = null;

        console.info('[zb-bridge] dữ liệu thật đã nạp:',
          ZB.customers.length + ' khách hàng,',
          ZB.proposals.length + ' proposal,',
          ZB.kbs.length + ' KB,',
          ZB.skills.length + ' skill,',
          ZB.tpls.length + ' template,',
          ZB.dir.length + ' danh bạ,',
          ZB.reqs.length + ' yêu cầu,',
          ZB.users.length + ' người dùng,',
          ZB.prods.length + ' sản phẩm,',
          ZB.notifs.length + ' thông báo,',
          ZB.topProds.length + ' sản phẩm được đề xuất nhiều');
        emit();
        return ZB;
      })
      .catch(function (e) {
        ZB.lastError = e.code || e.message;
        console.warn('[zb-bridge] không nạp được dữ liệu thật, giữ mock:', e.code || e.message);
        emit();
        throw e;
      });
  }

  ZB.loadAll = loadAll;

  // ── Ghi dữ liệu: mọi thứ tạo mới đều đi qua backend rồi nạp lại ──────────

  function refresh(result) {
    return loadAll().then(function () { return result; }, function () { return result; });
  }

  function fail(e) {
    ZB.lastError = e.code || e.message;
    console.warn('[zb-bridge] tạo mới thất bại:', e.code || e.message);
    return { ok: false, code: e.code, message: e.message };
  }

  function reloadNotifs() {
    return req('/notifications?limit=50').then(function (res) {
      ZB.notifs = mapNotifs(res);
      ZB.notifWeek = mapNotifWeek(res);
      ZB.lastError = null;
      emit();
      return { ok: true };
    }, function (e) {
      emit();
      return fail(e);
    });
  }

  ZB.reloadNotifs = reloadNotifs;

  ZB.setNotifRead = function (id, read) {
    var row = (ZB.notifs || []).find(function (n) { return n.id === id; });
    if (row) { row.read = !!read; emit(); }
    return req('/notifications/' + id, { method: 'PATCH', body: { read: !!read } })
      .then(reloadNotifs, function (e) { return reloadNotifs().then(function () { return fail(e); }); });
  };

  ZB.markAllRead = function () {
    (ZB.notifs || []).forEach(function (n) { n.read = true; });
    emit();
    return req('/notifications/read-all', { method: 'POST' })
      .then(reloadNotifs, function (e) { return reloadNotifs().then(function () { return fail(e); }); });
  };

  function hhmm(iso) {
    var d = iso ? new Date(iso) : null;
    if (!d || isNaN(d.getTime())) return '';
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }

  ZB.createCustomer = function (o) {
    var body = {
      name: o.name,
      website: o.website || null,
      industry: o.industry || null,
      knowledgeBaseIds: o.kbIds || [],
      startResearch: o.research !== false,
      depth: o.depth || 'standard'
    };
    return req('/customers', { method: 'POST', body: body })
      .then(function (r) {
        var c = r.customer || {};
        return refresh({
          ok: true,
          id: c.id,
          name: c.name,
          website: c.website || '',
          jobId: r.researchJobId || null
        });
      })
      .catch(fail);
  };

  ZB.rerunResearch = function (customerId, o) {
    o = o || {};
    return req('/customers/' + customerId + '/research', {
      method: 'POST',
      body: { knowledgeBaseIds: o.kbIds || [], depth: o.depth || 'standard' }
    }).then(function (r) { return { ok: true, jobId: r.jobId }; }, fail);
  };

  ZB.cancelResearch = function (jobId) {
    return req('/research-jobs/' + jobId + '/cancel', { method: 'POST' })
      .then(function (r) { return { ok: true, status: r.status }; }, fail);
  };

  var JOB_LOG_EVENTS = { step: 1, source: 1, error: 1 };

  // Job xong thì nạp lại cache MỘT lần: research đổi website/logo/trạng thái
  // của khách trên server, không refresh thì UI giữ logo cũ tới khi F5.
  var refreshedJobs = {};

  ZB.jobStatus = function (jobId) {
    return req('/jobs/' + jobId).then(function (j) {
      var events = j.events || [];
      if (j.status === 'done' && !refreshedJobs[jobId]) {
        refreshedJobs[jobId] = 1;
        loadAll().catch(function () {});
      }
      return {
        ok: true,
        status: j.status,
        done: j.status === 'done',
        failed: j.status === 'failed' || j.status === 'cancelled',
        progress: Math.round((j.progress || 0) * 100),
        stage: j.stageLabel || j.stage || '',
        error: (j.error && (j.error.message || j.error.code)) || null,
        result: j.result || null,
        log: events
          .filter(function (e) { return JOB_LOG_EVENTS[e.event]; })
          .map(function (e) {
            var d = e.data || {};
            return { time: hhmm(e.createdAt), text: e.message || d.label || d.url || e.event };
          }),
        sources: events
          .filter(function (e) { return e.event === 'source'; })
          .map(function (e) { return e.data || {}; })
      };
    }, function (e) {
      return { ok: false, code: e.code, message: e.message };
    });
  };

  ZB.customerDetail = function (id) {
    return req('/customers/' + id).then(function (c) {
      // Lớp augmentation (breadcrumb dịch vụ ở tab Matching) đọc từ đây.
      ZB.lastCust = c;
      return { ok: true, customer: c };
    }, function (e) {
      return { ok: false, code: e.code, message: e.message };
    });
  };

  var FACT_VERDICT = { ok: 'ok', no: 'rejected', rejected: 'rejected', ai: 'ai' };

  ZB.setFactVerdict = function (customerId, factId, verdict) {
    return req('/customers/' + customerId + '/facts/' + factId, {
      method: 'PATCH',
      body: { verdict: FACT_VERDICT[verdict] || 'ai' }
    }).then(function (f) { return { ok: true, fact: f }; }, fail);
  };

  var AUDIT_ACTIONS = {
    'auth.login': 'đăng nhập',
    'auth.bypass_login': 'đăng nhập',
    'auth.logout': 'đăng xuất',
    'customer.create': 'tạo khách hàng',
    'customer.update': 'cập nhật khách hàng',
    'customer.delete': 'xoá khách hàng',
    'customer.archive': 'lưu trữ khách hàng',
    'research.start': 'chạy Deep Research',
    'fact.verdict': 'duyệt dữ kiện',
    'handover.transfer': 'bàn giao khách hàng',
    'proposal.create': 'tạo proposal',
    'proposal.update': 'sửa proposal',
    'proposal.delete': 'xoá proposal',
    'proposal.duplicate': 'nhân bản proposal',
    'proposal.regenerate': 'sinh lại proposal',
    'proposal.restore': 'khôi phục proposal',
    'proposal.archive': 'lưu trữ proposal',
    'proposal.ai_apply': 'áp dụng gợi ý AI',
    'approval.submit': 'gửi duyệt proposal',
    'approval.approve': 'duyệt proposal',
    'approval.withdraw': 'thu hồi duyệt',
    'export.create': 'xuất file',
    'export.retry': 'xuất lại file',
    'comment.create': 'bình luận',
    'kb.create': 'tạo Knowledge Base',
    'kb.update': 'sửa Knowledge Base',
    'kb.delete': 'xoá Knowledge Base',
    'kb.sync': 'đồng bộ Knowledge Base',
    'knowledge.upload': 'tải tài liệu lên',
    'knowledge.delete_file': 'xoá tài liệu',
    'product.create': 'thêm sản phẩm',
    'product.update': 'cập nhật sản phẩm',
    'product.delete': 'xoá sản phẩm',
    'product.crawl': 'crawl Product Knowledge',
    'share.grant': 'chia sẻ',
    'share.link': 'tạo link chia sẻ',
    'role.update': 'đổi quyền vai trò',
    'invite.create': 'mời người dùng',
    'access_request.create': 'xin quyền truy cập',
    'access_request.approve': 'duyệt yêu cầu truy cập',
    'template.create': 'tạo template',
    'template.update': 'sửa template',
    'template.delete': 'xoá template',
    'template.import': 'nhập template',
    'template.restore': 'khôi phục template',
    'user.update': 'cập nhật người dùng',
    'user.delete': 'xoá người dùng',
    'user.lock': 'khoá tài khoản',
    'user.unlock': 'mở khoá tài khoản',
    'user.profile_update': 'cập nhật hồ sơ',
    'system.ai_config': 'đổi cấu hình AI',
    'system.policies': 'đổi chính sách hệ thống'
  };

  var AVATAR_COLORS = ['#0068FF', '#1A30A0', '#787CA6', '#12A150', '#E8A13C'];

  function colorFor(name) {
    var t = String(name || ''), n = 0;
    for (var i = 0; i < t.length; i++) n = (n + t.charCodeAt(i)) % 997;
    return AVATAR_COLORS[n % AVATAR_COLORS.length];
  }

  function stamp(iso) {
    var d = iso ? new Date(iso) : null;
    if (!d || isNaN(d.getTime())) return '';
    var two = function (x) { return ('0' + x).slice(-2); };
    return two(d.getDate()) + '/' + two(d.getMonth() + 1) + ' ' +
      two(d.getHours()) + ':' + two(d.getMinutes());
  }

  ZB.auditLog = function (o) {
    o = o || {};
    var qs = ['size=' + (o.size || 20)];
    if (o.category) qs.push('category=' + encodeURIComponent(o.category));
    if (o.q) qs.push('q=' + encodeURIComponent(o.q));
    if (o.days) {
      var from = new Date(Date.now() - o.days * 86400000);
      qs.push('from=' + encodeURIComponent(from.toISOString()));
    }
    return req('/admin/audit?' + qs.join('&')).then(function (r) {
      return {
        ok: true,
        total: r.total || 0,
        categories: r.categories || [],
        items: (r.items || []).map(function (a) {
          var who = (a.actor && (a.actor.name || a.actor.id)) || 'Hệ thống';
          return {
            id: a.id,
            time: stamp(a.createdAt),
            actor: who,
            init: (a.actor && a.actor.initials) || who.slice(0, 2).toUpperCase(),
            color: colorFor(who),
            action: AUDIT_ACTIONS[a.action] || a.action,
            target: a.resourceLabel || a.resourceType || '',
            detail: a.summary || '',
            cat: a.category || ''
          };
        })
      };
    }, function (e) {
      return { ok: false, code: e.code, message: e.message, items: [], categories: [] };
    });
  };

  ZB.auditDetail = function (id) {
    return req('/admin/audit/' + id).then(function (a) {
      return { ok: true, detail: a };
    }, function (e) {
      return { ok: false, code: e.code, message: e.message };
    });
  };

  ZB.createKb = function (o) {
    var body = {
      name: o.name,
      description: o.description || null,
      scope: o.shared ? 'Shared' : 'Mine',
      tags: o.tags || []
    };
    return req('/knowledge-bases', { method: 'POST', body: body })
      .then(function (kb) {
        if (!o.url) return kb;
        return req('/knowledge-bases/' + kb.id + '/sources', {
          method: 'POST', body: { url: o.url }
        }).then(function () { return kb; }, function () { return kb; });
      })
      .then(function (kb) { return refresh({ ok: true, id: kb.id, name: kb.name }); })
      .catch(fail);
  };

  ZB.createSkill = function (o) {
    var body = {
      name: o.name,
      description: o.description || null,
      stage: o.stage || 'Research',
      scope: o.shared ? 'Shared' : 'Mine',
      prompt: o.prompt || ''
    };
    return req('/skills', { method: 'POST', body: body })
      .then(function (s) { return refresh({ ok: true, id: s.id, name: s.name }); })
      .catch(fail);
  };

  ZB.saveSkill = function (o) {
    var body = {
      name: o.name,
      description: o.description || null,
      prompt: o.prompt || '',
      scope: o.shared ? 'Shared' : 'Mine'
    };
    if (o.id) {
      return req('/skills/' + o.id, { method: 'PATCH', body: body })
        .then(function (s) {
          return refresh({ ok: true, id: s.id, version: s.version || 'v1.0' });
        })
        .catch(fail);
    }
    body.stage = o.stage || 'Research';
    return req('/skills', { method: 'POST', body: body })
      .then(function (s) {
        return refresh({ ok: true, id: s.id, version: s.version || 'v1.0' });
      })
      .catch(fail);
  };

  ZB.skillVersions = {};

  ZB.loadSkillVersions = function (id) {
    if (!id) return Promise.resolve([]);
    if (ZB.skillVersions[id]) return Promise.resolve(ZB.skillVersions[id]);
    return req('/skills/' + id + '/versions')
      .then(function (r) {
        ZB.skillVersions[id] = (r.items || []).map(function (v) {
          return {
            ver: v.version,
            note: v.note || '',
            author: (v.author && (v.author.handle || v.author.name)) || '',
            when: ago(v.createdAt),
            iso: v.createdAt
          };
        });
        emit();
        return ZB.skillVersions[id];
      })
      .catch(function () { ZB.skillVersions[id] = []; return []; });
  };

  ZB.restoreSkillVersion = function (id, ver) {
    return req('/skills/' + id + '/versions/' + encodeURIComponent(ver) + '/restore', {
      method: 'POST'
    })
      .then(function (r) {
        delete ZB.skillVersions[id];
        return refresh({ ok: true, version: r.version || ver });
      })
      .catch(fail);
  };

  ZB.saveSkillTools = function (id, tools) {
    if (!id) return Promise.resolve({ ok: false, message: 'Thiếu id skill' });
    return req('/skills/' + id, { method: 'PATCH', body: { tools: tools } })
      .then(function (s) { return refresh({ ok: true, tools: s.tools || [] }); })
      .catch(fail);
  };

  ZB.importTemplateFiles = function (files) {
    if (!files || !files.length) return Promise.resolve({ ok: false, message: 'Chưa chọn file' });
    var headers = {};
    var t = token();
    if (t) headers.Authorization = 'Bearer ' + t;

    var results = [];
    var chain = Promise.resolve();
    for (var i = 0; i < files.length; i++) {
      chain = chain.then(
        (function (f) {
          return function () {
            var form = new FormData();
            form.append('file', f, f.name);
            return fetch(API + '/templates/import', {
              method: 'POST', headers: headers, body: form
            }).then(function (r) {
              return r.text().then(function (txt) {
                var data = null;
                try { data = txt ? JSON.parse(txt) : null; } catch (e) {}
                if (!r.ok) {
                  results.push({
                    name: f.name,
                    status: 'failed',
                    message: (data && data.error && data.error.message) || ('HTTP ' + r.status)
                  });
                } else {
                  results.push({
                    name: f.name,
                    status: 'ok',
                    templateId: data && data.id,
                    slides: (data && data.slideCount) || 0
                  });
                }
              });
            }).catch(function (e) {
              results.push({ name: f.name, status: 'failed', message: e.message });
            });
          };
        })(files[i])
      );
    }

    return chain.then(function () {
      var okCount = results.filter(function (r) { return r.status === 'ok'; }).length;
      return refresh({
        ok: okCount > 0,
        created: okCount,
        failed: results.length - okCount,
        items: results,
        message: okCount ? null : (results[0] && results[0].message)
      });
    });
  };

  ZB.pickTemplateFile = function () {
    return new Promise(function (resolve) {
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = '.pdf';
      input.multiple = true;
      input.style.display = 'none';
      document.body.appendChild(input);
      input.addEventListener('change', function () {
        var files = input.files;
        if (input.parentNode) document.body.removeChild(input);
        if (!files || !files.length) return resolve({ ok: false, cancelled: true });
        ZB.importTemplateFiles(files).then(resolve);
      });
      input.addEventListener('cancel', function () {
        if (input.parentNode) document.body.removeChild(input);
        resolve({ ok: false, cancelled: true });
      });
      input.click();
    });
  };

  ZB.templateVersions = {};

  ZB.loadTemplateVersions = function (id) {
    if (!id) return Promise.resolve([]);
    if (ZB.templateVersions[id]) return Promise.resolve(ZB.templateVersions[id]);
    return req('/templates/' + id + '/versions')
      .then(function (r) {
        ZB.templateVersions[id] = (r.items || []).map(function (v) {
          return {
            ver: v.version,
            note: v.note || '',
            author: (v.author && (v.author.handle || v.author.name)) || '',
            when: ago(v.createdAt)
          };
        });
        emit();
        return ZB.templateVersions[id];
      })
      .catch(function () { ZB.templateVersions[id] = []; return []; });
  };

  ZB.restoreTemplateVersion = function (id, ver) {
    return req('/templates/' + id + '/versions/' + encodeURIComponent(ver) + '/restore', {
      method: 'POST'
    })
      .then(function (t) {
        delete ZB.templateVersions[id];
        return refresh({ ok: true, version: t.version || ver });
      })
      .catch(fail);
  };

  ZB.templatePages = {};
  var pageCache = {};

  // Ảnh trang cũng cần Bearer, nên tải bằng fetch rồi đổi sang blob URL.
  // Chỉ tải trang đang xem, không tải cả deck 25 trang.
  ZB.pageImage = function (id, index) {
    var key = id + '#' + index;
    if (pageCache[key]) return pageCache[key];
    var headers = {};
    var tok = token();
    if (tok) headers.Authorization = 'Bearer ' + tok;
    pageCache[key] = null;
    fetch(API + '/templates/' + id + '/pages/' + index, { headers: headers })
      .then(function (r) { return r.ok ? r.blob() : null; })
      .then(function (b) {
        if (b) { pageCache[key] = URL.createObjectURL(b); emit(); }
      })
      .catch(function () {});
    return null;
  };

  ZB.pageImageReady = function (id, index) {
    return pageCache[id + '#' + index] || null;
  };

  ZB.loadTemplatePages = function (id) {
    if (!id) return Promise.resolve(null);
    if (ZB.templatePages[id]) return Promise.resolve(ZB.templatePages[id]);
    return req('/templates/' + id + '/pages')
      .then(function (r) {
        ZB.templatePages[id] = { total: r.total || 0, renderable: !!r.renderable, reason: r.reason };
        emit();
        return ZB.templatePages[id];
      })
      .catch(function () {
        ZB.templatePages[id] = { total: 0, renderable: false, reason: 'không đọc được' };
        return ZB.templatePages[id];
      });
  };

  ZB.templateFileUrl = function (id) {
    return req('/templates/' + id + '/file')
      .then(function (r) { return { ok: true, url: r.url, filename: r.filename, kind: r.kind }; })
      .catch(fail);
  };

  ZB.completeOnboarding = function () {
    if (ZB.me) {
      ZB.me.needsOnboarding = false;
      ZB.me.isFirstLogin = false;
    }
    return req('/me/onboarding', {
      method: 'PATCH',
      body: { completedSteps: ['profile', 'team', 'interests'] }
    }).then(function (r) { return { ok: true, needsOnboarding: r.needsOnboarding }; }, fail);
  };

  ZB.completeTour = function () {
    if (ZB.me) ZB.me.needsTour = false;
    return req('/me/onboarding', { method: 'PATCH', body: { tourCompleted: true } })
      .then(function (r) { return { ok: true, needsTour: r.needsTour }; }, fail);
  };


  ZB.importSkillFiles = function (files) {
    if (!files || !files.length) return Promise.resolve({ ok: false, message: 'Chưa chọn file' });
    var form = new FormData();
    for (var i = 0; i < files.length; i++) form.append('files', files[i], files[i].name);
    var headers = {};
    var t = token();
    if (t) headers.Authorization = 'Bearer ' + t;
    return fetch(API + '/skills/import', { method: 'POST', headers: headers, body: form })
      .then(function (r) {
        return r.text().then(function (txt) {
          var data = null;
          try { data = txt ? JSON.parse(txt) : null; } catch (e) {}
          if (!r.ok) {
            var msg = (data && data.error && data.error.message) || ('HTTP ' + r.status);
            return { ok: false, message: msg, code: data && data.error && data.error.code };
          }
          return refresh({
            ok: true,
            created: data.created,
            failed: data.failed,
            items: data.items || [],
            skills: data.skills || []
          });
        });
      })
      .catch(function (e) { return { ok: false, message: e.message }; });
  };

  ZB.pickSkillFile = function () {
    return new Promise(function (resolve) {
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = '.skill,.json,.md';
      input.multiple = true;
      input.style.display = 'none';
      document.body.appendChild(input);
      input.addEventListener('change', function () {
        var files = input.files;
        document.body.removeChild(input);
        if (!files || !files.length) return resolve({ ok: false, cancelled: true });
        ZB.importSkillFiles(files).then(resolve);
      });
      input.addEventListener('cancel', function () {
        if (input.parentNode) document.body.removeChild(input);
        resolve({ ok: false, cancelled: true });
      });
      input.click();
    });
  };

  // ── Knowledge Base: tài liệu thật ────────────────────────────────────────
  // Prototype dùng uploader mô phỏng (pool tên file cứng). Các hàm dưới đây nối
  // thẳng vào POST /knowledge-bases/{id}/files để file thật vào MinIO + DB.

  ZB.KB_EXTS = ['pdf', 'docx', 'xlsx', 'pptx'];
  ZB.KB_MAX_MB = 50;
  ZB.KB_ACCEPT = '.pdf,.docx,.xlsx,.pptx';

  ZB.kbFiles = {};
  var kbFilesBusy = {};

  function mapKbFiles(res) {
    return (res.items || []).map(function (f) {
      var mb = (f.sizeBytes || 0) / 1048576;
      return [
        f.name,
        (f.ext || '').toUpperCase(),
        mb.toFixed(1).replace('.', ',') + ' MB',
        (f.uploadedBy && (f.uploadedBy.handle || f.uploadedBy.name)) || '',
        f.id,
        f.status || ''
      ];
    });
  }

  ZB.loadKbFiles = function (kbId) {
    if (!kbId) return Promise.resolve([]);
    if (ZB.kbFiles[kbId]) return Promise.resolve(ZB.kbFiles[kbId]);
    if (kbFilesBusy[kbId]) return Promise.resolve([]);
    kbFilesBusy[kbId] = true;
    return req('/knowledge-bases/' + kbId + '/files')
      .then(function (r) {
        kbFilesBusy[kbId] = false;
        ZB.kbFiles[kbId] = mapKbFiles(r);
        emit();
        return ZB.kbFiles[kbId];
      })
      .catch(function () {
        kbFilesBusy[kbId] = false;
        ZB.kbFiles[kbId] = [];
        return [];
      });
  };

  // Trả về null nếu file hợp lệ, ngược lại là lý do từ chối (tiếng Việt, để UI hiện).
  ZB.checkKbFile = function (file) {
    var name = (file && file.name) || '';
    var ext = name.indexOf('.') > -1 ? name.split('.').pop().toLowerCase() : '';
    if (ZB.KB_EXTS.indexOf(ext) < 0) {
      return 'Định dạng .' + (ext || '?')
        + ' không được hỗ trợ — chỉ nhận PDF, DOCX, XLSX, PPTX.';
    }
    var mb = ((file && file.size) || 0) / 1048576;
    if (mb > ZB.KB_MAX_MB) {
      return 'Dung lượng ' + mb.toFixed(1).replace('.', ',') + ' MB vượt giới hạn '
        + ZB.KB_MAX_MB + ' MB — tách nhỏ file rồi tải lại.';
    }
    return null;
  };

  // Mở hộp thoại chọn file thật. Không upload — chỉ trả về danh sách File.
  ZB.pickKbFiles = function () {
    return new Promise(function (resolve) {
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = ZB.KB_ACCEPT;
      input.multiple = true;
      input.style.display = 'none';
      document.body.appendChild(input);
      input.addEventListener('change', function () {
        var picked = [];
        var list = input.files || [];
        for (var i = 0; i < list.length; i++) picked.push(list[i]);
        if (input.parentNode) document.body.removeChild(input);
        if (!picked.length) return resolve({ ok: false, cancelled: true, files: [] });
        resolve({ ok: true, files: picked });
      });
      input.addEventListener('cancel', function () {
        if (input.parentNode) document.body.removeChild(input);
        resolve({ ok: false, cancelled: true, files: [] });
      });
      input.click();
    });
  };

  ZB.uploadKbFiles = function (kbId, files) {
    if (!kbId) return Promise.resolve({ ok: false, message: 'Thiếu id Knowledge Base' });
    if (!files || !files.length) {
      return Promise.resolve({ ok: false, message: 'Chưa chọn file' });
    }
    var form = new FormData();
    for (var i = 0; i < files.length; i++) form.append('files', files[i], files[i].name);
    var headers = {};
    var t = token();
    if (t) headers.Authorization = 'Bearer ' + t;
    return fetch(API + '/knowledge-bases/' + kbId + '/files', {
      method: 'POST', headers: headers, body: form
    })
      .then(function (r) {
        return r.text().then(function (txt) {
          var data = null;
          try { data = txt ? JSON.parse(txt) : null; } catch (e) {}
          if (!r.ok) {
            return {
              ok: false,
              message: (data && data.error && data.error.message) || ('HTTP ' + r.status),
              code: data && data.error && data.error.code
            };
          }
          var items = (data && data.results) || [];
          var okCount = (data && data.okCount) || 0;
          delete ZB.kbFiles[kbId];
          return ZB.loadKbFiles(kbId).then(function () {
            return refresh({
              ok: okCount > 0,
              created: okCount,
              failed: (data && data.failedCount) || 0,
              items: items,
              message: okCount ? null : ((items[0] && items[0].message) || 'Không lưu được file')
            });
          });
        });
      })
      .catch(function (e) { return { ok: false, message: e.message }; });
  };

  // ── Xoá thật (prototype chỉ đổi state cục bộ nên reload là thấy lại) ─────

  function del(path, after) {
    return req(path, { method: 'DELETE' })
      .then(function () {
        if (after) after();
        return refresh({ ok: true });
      })
      .catch(fail);
  }

  ZB.deleteSkill = function (id) {
    if (!id) return Promise.resolve({ ok: false, message: 'Thiếu id skill' });
    return del('/skills/' + id, function () { delete ZB.skillVersions[id]; });
  };

  ZB.deleteKb = function (id) {
    if (!id) return Promise.resolve({ ok: false, message: 'Thiếu id Knowledge Base' });
    return del('/knowledge-bases/' + id, function () { delete ZB.kbFiles[id]; });
  };

  ZB.deleteKbFile = function (kbId, fileId) {
    if (!kbId || !fileId) return Promise.resolve({ ok: false, message: 'Thiếu id tài liệu' });
    return del('/knowledge-bases/' + kbId + '/files/' + fileId, function () {
      delete ZB.kbFiles[kbId];
    });
  };

  ZB.deleteTemplate = function (id) {
    if (!id) return Promise.resolve({ ok: false, message: 'Thiếu id template' });
    return del('/templates/' + id, function () { delete ZB.templateVersions[id]; });
  };

  ZB.deleteProposal = function (id) {
    if (!id) return Promise.resolve({ ok: false, message: 'Thiếu id proposal' });
    return del('/proposals/' + id);
  };

  ZB.deleteCustomer = function (id) {
    if (!id) return Promise.resolve({ ok: false, message: 'Thiếu id khách hàng' });
    return del('/customers/' + id);
  };

  // Xoá nhiều proposal theo tên hiển thị — trả về số xoá được / lỗi đầu tiên.
  ZB.deleteProposalsByTitle = function (titles) {
    var list = (ZB.proposals || []);
    var ids = (titles || []).map(function (t) {
      var hit = list.filter(function (p) { return p.title === t; })[0];
      return hit && hit.__id;
    }).filter(Boolean);
    if (!ids.length) return Promise.resolve({ ok: false, message: 'Không tìm thấy proposal' });

    var okCount = 0;
    var firstErr = null;
    var chain = Promise.resolve();
    ids.forEach(function (id) {
      chain = chain.then(function () {
        return req('/proposals/' + id, { method: 'DELETE' })
          .then(function () { okCount += 1; })
          .catch(function (e) { if (!firstErr) firstErr = e.message; });
      });
    });
    return chain.then(function () {
      return refresh({ ok: okCount > 0, deleted: okCount, message: firstErr });
    });
  };

  // Thẻ trong thư viện chỉ biết tên (libGone dùng kind::name làm khoá), nên phải
  // tra ngược ra id thật từ danh sách live trước khi gọi DELETE.
  ZB.deleteLibItem = function (kind, name) {
    var lists = { skill: ZB.skills, kb: ZB.kbs, tpl: ZB.tpls, prod: ZB.prods };
    var paths = {
      skill: '/skills/', kb: '/knowledge-bases/',
      tpl: '/templates/', prod: '/products/'
    };
    var path = paths[kind];
    if (!path) return Promise.resolve({ ok: false, message: 'không hỗ trợ loại ' + kind });

    var hit = (lists[kind] || []).filter(function (x) { return x.name === name; })[0];
    if (!hit || !hit.__id) {
      return Promise.resolve({ ok: false, message: 'không tìm thấy “' + name + '” trong dữ liệu live' });
    }
    return del(path + hit.__id, function () {
      if (kind === 'skill') delete ZB.skillVersions[hit.__id];
      if (kind === 'kb') delete ZB.kbFiles[hit.__id];
      if (kind === 'tpl') delete ZB.templateVersions[hit.__id];
    });
  };

  // Hàng khách hàng dùng c.key, vốn chính là customer id.
  ZB.deleteCustomers = function (keys) {
    var ids = (keys || []).filter(Boolean);
    if (!ids.length) return Promise.resolve({ ok: false, message: 'chưa chọn khách hàng' });

    var okCount = 0;
    var firstErr = null;
    var chain = Promise.resolve();
    ids.forEach(function (id) {
      chain = chain.then(function () {
        return req('/customers/' + id, { method: 'DELETE' })
          .then(function () { okCount += 1; })
          .catch(function (e) { if (!firstErr) firstErr = e.message; });
      });
    });
    return chain.then(function () {
      return refresh({ ok: okCount > 0, deleted: okCount, message: firstErr });
    });
  };

  // ── Product Matching: phương án thật từ worker LLM ──────────────────────
  // Prototype dùng _MATCH() cứng. Các hàm dưới nối vào job matching thật và
  // GET /customers/{id}/solutions, rồi map sang đúng shape prototype đang đọc.

  ZB.solutions = {};
  var solBusy = {};

  function planRole(score) { return score >= 80 ? 'Chính' : 'Bổ trợ'; }

  function mapSolutions(res) {
    var rows = (res.items || []).filter(function (s) { return s.product; });
    var ranked = rows.slice().sort(function (a, b) {
      return (b.fitScore || 0) - (a.fitScore || 0);
    });

    var primary = ranked.map(function (s) {
      var tags = [];
      if (s.package) tags.push(s.package);
      if (s.expectedImpact) tags.push(s.expectedImpact);
      if (s.pricingNote) tags.push(s.pricingNote);
      return {
        name: s.product.name,
        score: Math.round(s.fitScore || 0),
        why: s.rationale || s.title || '',
        tags: tags.slice(0, 3),
        painpoint: s.painpoint || '',
        opportunity: s.opportunity || '',
        __sid: s.id,
        __selected: !!s.selected,
        __evi: (s.evidence || []).map(function (e) { return e && e.id; }).filter(Boolean)
      };
    });

    // “extra” = sản phẩm trong danh mục nhưng worker không đề xuất -> chưa có điểm
    var seen = {};
    primary.forEach(function (p) { seen[p.name] = 1; });
    var extra = (ZB.prods || [])
      .filter(function (p) { return !seen[p.name]; })
      .map(function (p) {
        return { name: p.name, score: 0, why: p.desc || p.sub || '' };
      });

    // plan: ưu tiên những phương án đã chọn trong DB; chưa chọn gì thì lấy các
    // phương án đạt ngưỡng để màn hình có nội dung ngay sau khi chạy.
    var chosen = ranked.filter(function (s) { return s.selected; });
    if (!chosen.length) {
      chosen = ranked.filter(function (s) { return (s.fitScore || 0) >= 50; });
    }
    var plan = chosen.map(function (s) {
      return [s.product.name, planRole(s.fitScore || 0)];
    });

    return { primary: primary, extra: extra, plan: plan, raw: rows };
  }

  ZB.loadSolutions = function (customerId) {
    if (!customerId) return Promise.resolve(null);
    if (ZB.solutions[customerId]) return Promise.resolve(ZB.solutions[customerId]);
    if (solBusy[customerId]) return Promise.resolve(null);
    solBusy[customerId] = true;
    return req('/customers/' + customerId + '/solutions')
      .then(function (r) {
        solBusy[customerId] = false;
        ZB.solutions[customerId] = mapSolutions(r);
        emit();
        return ZB.solutions[customerId];
      })
      .catch(function () {
        solBusy[customerId] = false;
        ZB.solutions[customerId] = { primary: [], extra: [], plan: [], raw: [] };
        return ZB.solutions[customerId];
      });
  };

  ZB.reloadSolutions = function (customerId) {
    delete ZB.solutions[customerId];
    return ZB.loadSolutions(customerId);
  };

  ZB.runProductMatching = function (customerId) {
    if (!customerId) return Promise.resolve({ ok: false, message: 'Thiếu id khách hàng' });
    return req('/customers/' + customerId + '/product-matching', { method: 'POST' })
      .then(function (r) { return { ok: true, jobId: r.jobId }; }, fail);
  };

  ZB.setSolutionInPlan = function (customerId, name, inPlan) {
    var cache = ZB.solutions[customerId];
    if (!cache) return Promise.resolve({ ok: false, message: 'chưa tải phương án' });
    var rows = cache.raw || [];
    var hit = rows.filter(function (s) {
      return s.product && s.product.name === name;
    })[0];
    if (!hit) {
      return Promise.resolve({ ok: false, message: 'không có phương án nào cho ' + name });
    }

    function pick(id, on) {
      return req('/customers/' + customerId + '/solutions/' + id
        + '/select?selected=' + (on ? 'true' : 'false'), { method: 'POST' });
    }

    // Lần đầu người dùng chỉnh: ghi cứng phần plan đang hiển thị (vốn suy ra từ
    // ngưỡng điểm) để `selected` trong DB trở thành nguồn sự thật duy nhất.
    var chain = Promise.resolve();
    if (!rows.some(function (s) { return s.selected; })) {
      var implicit = {};
      (cache.plan || []).forEach(function (p) { implicit[p[0]] = 1; });
      rows.filter(function (s) {
        return s.product && implicit[s.product.name] && s.product.name !== name;
      }).forEach(function (s) {
        chain = chain.then(function () { return pick(s.id, true).catch(function () {}); });
      });
    }

    return chain
      .then(function () { return pick(hit.id, inPlan); })
      .then(function () { return ZB.reloadSolutions(customerId); })
      .then(function () { return { ok: true }; })
      .catch(fail);
  };

  ZB.exportSkillUrl = function (id) { return API + '/skills/' + id + '/export'; };

  // ── Auth hooks used by the patched prototype ─────────────────────────────

  ZB.requestOtp = function (email) {
    return req('/auth/otp/request', { method: 'POST', body: { email: email } })
      .then(function (r) {
        // Email trong OTP_BYPASS_EMAILS: server cấp token luôn, vào thẳng.
        if (r && r.direct && r.token) {
          setToken(r.token, r.refreshToken);
          return loadAll().then(function () {
            return { ok: true, direct: true, needsOnboarding: !!r.needsOnboarding, needsTour: !!r.needsTour, user: r.user };
          }, function () {
            return { ok: true, direct: true, needsOnboarding: !!r.needsOnboarding, needsTour: !!r.needsTour, user: r.user };
          });
        }
        return { ok: true, ttl: r.ttlSeconds, resendAfter: r.resendAfterSeconds };
      })
      .catch(function (e) {
        var kind = 'net';
        if (e.code === 'AUTH_DOMAIN') kind = 'domain';
        else if (e.code === 'AUTH_UNKNOWN') kind = 'unknown';
        else if (e.code === 'AUTH_503') kind = 'net';
        return { ok: false, kind: kind, code: e.code, message: e.message };
      });
  };

  ZB.verifyOtp = function (email, code) {
    return req('/auth/otp/verify', { method: 'POST', body: { email: email, code: code } })
      .then(function (r) {
        setToken(r.token, r.refreshToken);
        return loadAll().then(function () {
          return { ok: true, needsOnboarding: !!r.needsOnboarding, needsTour: !!r.needsTour, user: r.user };
        }, function () {
          return { ok: true, needsOnboarding: !!r.needsOnboarding, needsTour: !!r.needsTour, user: r.user };
        });
      })
      .catch(function (e) {
        var kind = 'bad';
        if (e.code === 'OTP_EXPIRED') kind = 'expired';
        else if (e.code === 'OTP_LOCKED') kind = 'locked';
        return { ok: false, kind: kind, code: e.code, message: e.message };
      });
  };

  ZB.resendOtp = function (email) { return ZB.requestOtp(email); };

  ZB.logout = function () {
    var t = token();
    setToken(null);
    ZB.ready = false;
    ZB.customers = ZB.proposals = ZB.kbs = ZB.skills = ZB.tpls = ZB.dir = null;
    ZB.reqs = ZB.users = ZB.prods = null;
    ZB.notifs = ZB.notifWeek = ZB.topProds = null;
    ZB.skillVersions = {};
    ZB.templateVersions = {};
    ZB.templatePages = {};
    pageCache = {};
    if (t) req('/auth/logout', { method: 'POST' }).catch(function () {});
    emit();
  };

  // Already signed in from a previous session: warm the cache immediately.
  if (token()) {
    loadAll().catch(function () { setToken(null); });
  }

  console.info('[zb-bridge] sẵn sàng, API =', API);
})();
