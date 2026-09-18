(function (P) {
  P = P || {};
  function str(v) {
    return (v === null || v === undefined ? "" : String(v)).trim();
  }
  function nid(v) {
    return String(v || "")
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "");
  }
  function nlab(v) {
    return String(v || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .replace(/\b(required|optional)\b/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }
  var DENY =
    /reference|emergency|previous|prior|former|school|university|college|citizenship|passport|password|referral|referred|employer|salary|cover letter|describe|explain/;
  var AC = {
    "given-name": "first_name",
    "family-name": "last_name",
    name: "full_name",
    email: "email",
    "address-level2": "city",
    "address-level1": "region",
    country: "country",
    "country-name": "country",
    "postal-code": "postal_code",
    "organization-title": "headline",
  };
  var IDS = {
    firstname: "first_name",
    givenname: "first_name",
    lastname: "last_name",
    familyname: "last_name",
    surname: "last_name",
    fullname: "full_name",
    yourname: "full_name",
    applicantname: "full_name",
    name: "full_name",
    email: "email",
    emailaddress: "email",
    phone: "phone",
    phonenumber: "phone",
    mobile: "phone",
    mobilenumber: "phone",
    cell: "phone",
    cellphone: "phone",
    telephone: "phone",
    city: "city",
    currentcity: "city",
    homecity: "city",
    region: "region",
    state: "region",
    province: "region",
    country: "country",
    countryname: "country",
    postalcode: "postal_code",
    postcode: "postal_code",
    postal: "postal_code",
    zip: "postal_code",
    zipcode: "postal_code",
    linkedin: "linkedin",
    linkedinurl: "linkedin",
    linkedinprofile: "linkedin",
    portfolio: "portfolio",
    portfoliourl: "portfolio",
    website: "portfolio",
    personalwebsite: "portfolio",
    personalsite: "portfolio",
    github: "github",
    githuburl: "github",
    githubprofile: "github",
    headline: "headline",
    currenttitle: "headline",
    currentrole: "headline",
    jobtitle: "headline",
  };
  var LABS = {
    "first name": "first_name",
    "given name": "first_name",
    "last name": "last_name",
    "family name": "last_name",
    surname: "last_name",
    "full name": "full_name",
    "your name": "full_name",
    "applicant name": "full_name",
    name: "full_name",
    email: "email",
    "email address": "email",
    "e mail": "email",
    phone: "phone",
    "phone number": "phone",
    mobile: "phone",
    "mobile number": "phone",
    cell: "phone",
    "cell phone": "phone",
    telephone: "phone",
    city: "city",
    "current city": "city",
    "home city": "city",
    state: "region",
    region: "region",
    province: "region",
    country: "country",
    zip: "postal_code",
    "zip code": "postal_code",
    "postal code": "postal_code",
    "post code": "postal_code",
    linkedin: "linkedin",
    "linkedin url": "linkedin",
    "linkedin profile": "linkedin",
    "linkedin profile url": "linkedin",
    portfolio: "portfolio",
    "portfolio url": "portfolio",
    website: "portfolio",
    "personal website": "portfolio",
    "personal site": "portfolio",
    github: "github",
    "github url": "github",
    "github profile": "github",
    headline: "headline",
    "current title": "headline",
    "current role": "headline",
    "job title": "headline",
  };
  function val(k) {
    var v = str(P[k]);
    return v || null;
  }
  function labelOf(el) {
    try {
      var a = el.getAttribute && el.getAttribute("aria-label");
      if (str(a)) return str(a);
      if (el.id) {
        var l = null;
        try {
          l = document.querySelector(
            'label[for="' + String(el.id).replace(/"/g, "") + '"]',
          );
        } catch (e) {
          l = null;
        }
        if (l && str(l.textContent)) return str(l.textContent).slice(0, 160);
      }
      var q = el.closest ? el.closest("label") : null;
      if (q && str(q.textContent)) return str(q.textContent).slice(0, 160);
      var ph = el.getAttribute && el.getAttribute("placeholder");
      if (str(ph)) return str(ph).slice(0, 160);
      return str(el.name) || str(el.id);
    } catch (e) {
      return "";
    }
  }
  function sectionBad(el) {
    try {
      var c = el.closest ? el.closest("fieldset,section,[role=group]") : null;
      if (!c) return false;
      var h = c.querySelector ? c.querySelector("legend,h1,h2,h3,h4") : null;
      var t = str((h && h.textContent) || c.textContent)
        .slice(0, 300)
        .toLowerCase();
      return DENY.test(t);
    } catch (e) {
      return false;
    }
  }
  function pick(el, label) {
    if (
      DENY.test(String(label || "").toLowerCase()) ||
      DENY.test(String(el.name || "").toLowerCase()) ||
      sectionBad(el)
    )
      return null;
    var ac = el.getAttribute
      ? str(el.getAttribute("autocomplete")).toLowerCase()
      : "";
    if (ac) {
      var tok = ac.split(/\s+/).filter(Boolean);
      var last = tok.length ? tok[tok.length - 1] : "";
      if (last === "tel") return val("phone");
      if (last.indexOf("tel-") === 0 || last === "country") return null;
      if (AC[last]) return val(AC[last]);
    }
    var n = nid(el.name) + "|" + nid(el.id);
    for (var k in IDS) {
      if (nid(el.name) === k || nid(el.id) === k) return val(IDS[k]);
    }
    var nl = nlab(label);
    if (DENY.test(String(label || "").toLowerCase())) return null;
    if (sectionBad(el)) return null;
    if (!nl || nl.length > 48) return null;
    if (!Object.prototype.hasOwnProperty.call(LABS, nl)) return null;
    return val(LABS[nl]);
  }
  function vis(el) {
    if (el.hidden || !el.getClientRects().length) return false;
    try {
      var cs = getComputedStyle(el);
      if (
        cs &&
        (cs.display === "none" ||
          cs.visibility === "hidden" ||
          cs.visibility === "collapse")
      )
        return false;
    } catch (e) {}
    return true;
  }
  function setVal(el, v) {
    try {
      var proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
      var d = proto && Object.getOwnPropertyDescriptor(proto, "value");
      if (d && d.set) d.set.call(el, v);
      else el.value = v;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    } catch (e) {
      try {
        el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      } catch (e2) {
        return false;
      }
    }
  }
  function report(n) {
    var msg =
      n > 0
        ? "Filled " +
          n +
          (n === 1 ? " field." : " fields.") +
          " Review every value. Uploads, custom dropdowns and other questions need your input."
        : "No matching fields found. Review every value. Uploads, custom dropdowns and other questions need your input.";
    try {
      var ex = document.getElementById("jobpilot-fill-status");
      if (ex) {
        var s = ex.querySelector("[data-msg]");
        if (s) s.textContent = msg;
        else ex.textContent = msg;
        return n;
      }
      var d = document.createElement("div");
      d.id = "jobpilot-fill-status";
      d.setAttribute("role", "status");
      d.setAttribute("aria-live", "polite");
      d.style.cssText =
        "position:fixed;top:12px;right:12px;z-index:999999;max-width:320px;background:#10261a;color:#fff;border:1px solid #2ea043;border-radius:10px;padding:10px 12px;font:14px/1.4 -apple-system,sans-serif;";
      var sp = document.createElement("span");
      sp.setAttribute("data-msg", "");
      sp.textContent = msg;
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = "Dismiss";
      b.style.cssText =
        "margin-left:10px;background:#238636;color:#fff;border:0;border-radius:6px;padding:8px 12px;min-height:44px;font:inherit;";
      b.addEventListener("click", function () {
        d.remove();
      });
      d.appendChild(sp);
      d.appendChild(b);
      document.body.appendChild(d);
    } catch (e) {}
    return n;
  }
  var n = 0;
  try {
    var els = document.querySelectorAll("input");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var t = str(el.type || "text").toLowerCase();
      if (t !== "text" && t !== "email" && t !== "tel" && t !== "url") continue;
      if (el.disabled || el.readOnly) continue;
      if (!vis(el)) continue;
      if (str(el.value)) continue;
      var v = pick(el, labelOf(el));
      if (v && setVal(el, v)) n++;
    }
  } catch (e) {}
  report(n);
  return n;
});
