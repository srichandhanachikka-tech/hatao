let mem = null;
let ver = null;
let chatMemoryId = null;

const $ = x => document.getElementById(x);

const STORE_MAP = {
  "Original Memory": "original",
  "Summary Store": "summary",
  "Embedding Store": "embedding",
  "Cache": "cache",
  "Downstream Copy": "downstream"
};


// ============================================================
// API
// ============================================================

function errDetail(d, status) {
  let msg = d && d.detail;

  if (Array.isArray(msg)) {
    msg = msg.map(x => x.msg || JSON.stringify(x)).join("; ");
  }

  if (typeof msg === "object" && msg) {
    msg = msg.msg || JSON.stringify(msg);
  }

  return msg || ("Request failed (" + status + ")");
}


async function api(url, options = {}) {
  let response;

  try {
    response = await fetch(url, options);
  } catch (e) {
    throw Error(
      "Cannot reach the HATAO server. Confirm the backend is running."
    );
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw Error(errDetail(data, response.status));
  }

  return data;
}


// ============================================================
// UI HELPERS
// ============================================================

function toast(message) {
  const t = $("toast");

  if (!t) return;

  t.textContent = message;
  t.classList.add("show");

  setTimeout(() => {
    t.classList.remove("show");
  }, 2800);
}


function page(pageName) {
  document
    .querySelectorAll(".page")
    .forEach(x => x.classList.remove("active"));

  document
    .querySelectorAll(".nav")
    .forEach(x => x.classList.remove("active"));

  const pageElement = $(pageName);

  if (pageElement) {
    pageElement.classList.add("active");
  }

  const navElement = document.querySelector(
    `[data-page="${pageName}"]`
  );

  if (navElement) {
    navElement.classList.add("active");
  }

  if (pageName === "dashboard") {
    dash();
  }

  if (pageName === "evaluation") {
    evalPage();
  }

  if (pageName === "audit") {
    loadAudit();
  }
}


document.querySelectorAll(".nav").forEach(x => {
  x.onclick = () => page(x.dataset.page);
});


function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    m => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;"
    }[m] || m)
  );
}


function bubble(text, className = "bubble") {
  const log = $("log");

  if (!log) return;

  const div = document.createElement("div");

  div.className = className;
  div.textContent = text;

  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}


function demo(text) {
  const input = $("text");

  if (!input) return;

  input.value = text;
  input.focus();
}


function retrievalText(code, label) {
  if (label) return label;

  return code === "YES"
    ? "YES — Memory is still retrievable"
    : "NO — Memory cannot be retrieved";
}


// ============================================================
// MEMORY RENDERING
// ============================================================

function renderMemory(data) {
  if (!data || !data.memory) return;

  mem = data.memory;

  const mid = $("mid");

  if (mid) {
    mid.textContent = mem.id;
  }

 const delButton = $("del");

if (delButton) {
  delButton.disabled = false;
  delButton.onclick = del;
}

  if (data.stores) {
    data.stores.forEach(store => {
      const element = $("n-" + STORE_MAP[store.store_name]);

      if (element) {
        element.textContent =
          store.present ? "ACTIVE" : "DELETED";
      }
    });
  }
}


// ============================================================
// CHATBOT
// ============================================================

async function sendChat() {
  const input = $("text");

  if (!input) return;

  const message = input.value.trim();

  if (!message) {
    toast("Type a message first.");
    return;
  }

  bubble(message, "bubble user");

  input.value = "";

  try {

    const data = await api("/api/chat", {
      method: "POST",

      headers: {
        "Content-Type": "application/json"
      },

      body: JSON.stringify({
        message: message,
        memory_id: chatMemoryId
      })
    });

    chatMemoryId = data.memory_id;

    bubble(data.reply, "bubble bot");

    // If HATAO detected information,
    // update the memory ID and architecture.
    if (data.type === "memory_created") {

      try {
        const memoryData = await api(
          "/api/memories/" + chatMemoryId
        );

        renderMemory(memoryData);

      } catch (e) {
        console.log("Memory refresh:", e.message);
      }
    }

    // If this is a deletion request,
    // automatically run verification.
    if (data.type === "deletion_request") {

      await performChatDeletion();
    }

  } catch (e) {
    bubble(
      "Sorry, I couldn't process that request.",
      "bubble bot"
    );

    toast(e.message);
  }
}


// ============================================================
// CHAT DELETION
// ============================================================

async function performChatDeletion() {

  if (!chatMemoryId) {
    toast("No conversation memory found.");
    return;
  }

  try {

    const scenario = "multiple";

    const data = await api(
      "/api/chat/delete",
      {
        method: "POST",

        headers: {
          "Content-Type": "application/json"
        },

        body: JSON.stringify({
          memory_id: chatMemoryId,
          scenario: scenario
        })
      }
    );

    ver = data.verification;

    renderMemory(data.memory);

    renderVer(ver);

    renderTrace(data.trace);

    const result = $("result");

    if (result) {
      result.classList.remove("hidden");
    }

    // ----------------------------------------------------------
    // SHOW VERIFICATION DIRECTLY IN CHAT
    // ----------------------------------------------------------

    const verification = data.verification.verification;
    const results = data.verification.results || [];

    const residual = results.filter(
      r => r.result === "FOUND"
    );

    let verificationMessage =
      "🔍 DELETION VERIFICATION\n\n";

    results.forEach(r => {

      verificationMessage +=
        `${r.result === "DELETED" ? "✅" : "❌"} ` +
        `${r.store_name}: ` +
        `${r.result === "DELETED" ? "DELETED" : "FOUND"}\n`;

    });

    verificationMessage +=
      `\nDeletion Completeness: ${verification.completeness}%` +
      `\nResidual Risk: ${verification.risk}` +
      `\nCoverage: ${
        verification.coverage ||
        ((verification.total_count || results.length) + "/5 stores checked")
      }`;

    verificationMessage +=
      residual.length
        ? `\n\n⚠️ Residual information detected in:\n` +
          residual
            .map(r => `• ${r.store_name}`)
            .join("\n")
        : "\n\n✅ No residual information detected.";

    verificationMessage +=
      `\n\nHuman Review: ${
        verification.status === "VERIFIED"
          ? "Not required"
          : "Required"
      }`;

    bubble(
      verificationMessage,
      "bubble bot"
    );

    await dash();

    toast("Deletion verification complete.");

  } catch (e) {

    console.error("Deletion verification error:", e);

    bubble(
      "I couldn't complete the deletion verification.",
      "bubble bot"
    );

    toast(e.message);
  }
}
// ENTER KEY
// ============================================================

const chatInput = $("text");

if (chatInput) {

  chatInput.addEventListener(
    "keydown",
    event => {

      if (
        event.key === "Enter" &&
        !event.shiftKey
      ) {

        event.preventDefault();

        sendChat();
      }
    }
  );
}


// ============================================================
// STORE MEMORY BUTTON
// ============================================================

async function store() {

  const input = $("text");

  if (!input) return;

  const content = input.value.trim();

  if (!content) {
    toast("Enter synthetic test information first.");
    return;
  }

  try {

    const data = await api(
      "/api/memories",
      {
        method: "POST",

        headers: {
          "Content-Type": "application/json"
        },

        body: JSON.stringify({
          content: content
        })
      }
    );

    renderMemory(data);

    chatMemoryId = data.memory.id;

    bubble(
      content,
      "bubble user"
    );

    bubble(
      "Memory stored successfully.",
      "bubble bot"
    );

    input.value = "";

    toast("Synthetic memory created.");

    await dash();

  } catch (e) {
    toast(e.message);
  }
}


// ============================================================
// IMAGE UPLOAD
// ============================================================

async function upload() {

  const imageInput = $("img");

  if (!imageInput) return;

  const file = imageInput.files[0];

  if (!file) return;

  const formData = new FormData();

  formData.append("file", file);

  try {

    const data = await api(
      "/api/memories/image",
      {
        method: "POST",
        body: formData
      }
    );

    renderMemory(data);

    chatMemoryId = data.memory.id;

    bubble(
      `Test image: ${file.name}`,
      "bubble user"
    );

    bubble(
      `Image memory ${data.memory.id} created.`,
      "bubble bot"
    );

    toast("Test image stored.");

    await dash();

  } catch (e) {

    toast(e.message);

  }

  imageInput.value = "";
}


// ============================================================
// OLD DELETE BUTTON
// ============================================================

async function del() {

  if (!chatMemoryId && !mem) {
    toast("No memory selected.");
    return;
  }

  const id = chatMemoryId || mem.id;

  try {

    const data = await api(
      "/api/deletion-requests/" + id,
      {
        method: "POST"
      }
    );

    chatMemoryId = id;
    ver = data.verification;

    renderMemory(data.memory);

    renderVer(ver);

    renderTrace(data.trace);

    const result = $("result");

    if (result) {
      result.classList.remove("hidden");
    }

    bubble(
      "Deletion request received. HATAO is checking the simulated storage paths.",
      "bubble bot"
    );

    await dash();

    toast("Verification complete.");

  } catch (e) {

    toast(e.message);

  }
}


// ============================================================
// VERIFICATION RESULT
// ============================================================

function renderVer(v) {

  if (!v) return;

  const verification = v.verification;
  const results = v.results || [];

  const residual = results.filter(
    r => r.result === "FOUND"
  );

  const retrievalCode =
    verification.retrieval;

  const score = $("score");

  if (score) {
    score.textContent =
      verification.completeness + "%";
  }

  const retrieval = $("retrieval");

  if (retrieval) {

    retrieval.textContent =
      retrievalText(
        retrievalCode,
        verification.retrieval_label ||
        v.retrieval_label
      );

    retrieval.className =
      retrievalCode === "YES"
        ? "bad"
        : "good";
  }

  const risk = $("rrisk");

  if (risk) {

    risk.textContent =
      verification.risk;

    risk.className =
      verification.risk === "LOW"
        ? "good"
        : verification.risk === "MEDIUM"
          ? "warn"
          : "bad";
  }

  const count = $("rcount");

  if (count) {

    count.textContent =
      residual.length
        ? residual
            .map(r => r.store_name)
            .join(", ")
        : "None";
  }

  const coverage = $("rcover");

  if (coverage) {

    coverage.textContent =
      verification.coverage ||
      v.coverage ||
      (
        (verification.total_count || results.length)
        + "/5 stores checked"
      );
  }

  const review = $("rreview");

  if (review) {

    review.textContent =
      verification.status === "VERIFIED"
        ? "Not required"
        : "Required";

    review.className =
      verification.status === "VERIFIED"
        ? "good"
        : "warn";
  }

  const title = $("rtitle");

  if (title) {

    title.textContent =
      verification.status === "VERIFIED"
        ? "Deletion verified"
        : "Residual memory detected";
  }


  // ----------------------------------------------------------
  // STORAGE TABLE
  // ----------------------------------------------------------

  const stores = $("stores");

  if (stores) {

    stores.innerHTML =
      '<div class="store">' +
      '<span>Storage path</span>' +
      '<span>Status</span>' +
      '<span>Retrieval</span>' +
      '</div>' +

      results.map(r =>

        `<div class="store">
          <span>${esc(r.store_name)}</span>

          <span class="${
            r.result === "DELETED"
              ? "good"
              : "bad"
          }">
            ${
              r.result === "DELETED"
                ? "✓ DELETED"
                : "✕ FOUND"
            }
          </span>

          <span class="${
            r.retrievable
              ? "bad"
              : "good"
          }">
            ${
              r.retrievable
                ? "YES"
                : "NO"
            }
          </span>
        </div>`

      ).join("");
  }


  // ----------------------------------------------------------
  // EVIDENCE
  // ----------------------------------------------------------

  const evidence = $("evidence");

  if (evidence) {

    if (residual.length) {

      evidence.innerHTML =
        "<b>Residual Risk & Evidence</b><br>" +

        "Residual memory detected in <b>" +

        residual
          .map(r => esc(r.store_name))
          .join(", ") +

        "</b>.<br><br>" +

        residual
          .map(
            r =>
              `• ${esc(r.store_name)} — confidence ${
                (r.confidence * 100).toFixed(0)
              }% — ${esc(r.evidence)}`
          )
          .join("<br>") +

        `<br><br>
        Deletion completeness:
        <b>${verification.completeness}%</b>
        · Retrieval:
        <b>${esc(
          retrievalText(
            retrievalCode,
            verification.retrieval_label
          )
        )}</b>
        · Risk:
        <b>${verification.risk}</b>`;
    }

    else {

      evidence.innerHTML =
        "<b>Evidence</b><br>" +
        "No retrievable synthetic memory was found " +
        "in any simulated store.";
    }
  }


  // ----------------------------------------------------------
  // FINDING
  // ----------------------------------------------------------

  const finding = $("finding");

  if (finding) {

    if (residual.length) {

      finding.innerHTML =
        "<b>Residual stores:</b> " +

        residual
          .map(r => esc(r.store_name))
          .join(", ") +

        "<br><b>Evidence:</b><br>" +

        residual
          .map(
            r =>
              `• ${esc(r.evidence)}`
          )
          .join("<br>");

    } else {

      finding.innerHTML = "";
    }
  }


  // ----------------------------------------------------------
  // HUMAN REVIEW
  // ----------------------------------------------------------

  const reviewBox = $("reviewbox");

  if (reviewBox) {

    reviewBox.classList.toggle(
      "hidden",
      verification.status === "VERIFIED"
    );
  }
}


// ============================================================
// INFORMATION TRACE
// ============================================================

function renderTrace(trace) {

  if (!trace || !trace.length) {
    return;
  }

  /*
  If the existing HTML already has a trace element,
  populate it.

  Otherwise the information is still available through
  the verification/audit APIs.
  */

  const traceElement =
    $("trace");

  if (!traceElement) {
    return;
  }

  traceElement.innerHTML =
    trace.map(item => {

      const residualStores =
        item.stores.filter(
          s => s.present
        );

      const allStores =
        item.stores.map(
          s =>
            `<div class="store">
              <span>${esc(s.store_name)}</span>
              <span class="${
                s.present
                  ? "bad"
                  : "good"
              }">
                ${
                  s.present
                    ? "✕ FOUND"
                    : "✓ DELETED"
                }
              </span>
            </div>`
        ).join("");

      return `
        <div class="case">
          <b>${esc(item.item_key)}</b>
          :
          ${esc(item.item_value)}

          <br><br>

          ${allStores}

          ${
            residualStores.length
              ? `<br>
                 <b class="bad">
                   Still retrievable from:
                 </b>
                 ${residualStores
                   .map(s => esc(s.store_name))
                   .join(", ")}`
              : `<br>
                 <b class="good">
                   No residual copy detected.
                 </b>`
          }
        </div>
      `;

    }).join("");
}


// ============================================================
// SCENARIOS
// ============================================================

async function scenario(name) {

  try {

    const data = await api(
      "/api/demo/scenario/" + name,
      {
        method: "POST"
      }
    );

    renderMemory(data.memory);

    chatMemoryId =
      data.memory.memory.id;

    ver = data.verification;

    renderVer(ver);

    renderTrace(
      data.trace ||
      data.verification.trace
    );

    const result = $("result");

    if (result) {
      result.classList.remove("hidden");
    }

    page("chat");

    await dash();

    toast(
      name + " scenario verified."
    );

  } catch (e) {

    toast(e.message);

  }
}


// ============================================================
// HUMAN REVIEW
// ============================================================

async function submitReview() {

  if (!ver) {
    toast("Run a verification first.");
    return;
  }

  const reviewer =
    $("reviewer");

  if (!reviewer) return;

  const name =
    reviewer.value.trim();

  if (!name) {
    toast("Enter reviewer name.");
    return;
  }

  try {

    await api(
      "/api/reviews",
      {
        method: "POST",

        headers: {
          "Content-Type": "application/json"
        },

        body: JSON.stringify({

          verification_id:
            ver.verification.id,

          reviewer:
            name,

          decision:
            $("decision").value,

          comments:
            $("comments").value

        })
      }
    );

    const reviewBox =
      $("reviewbox");

    if (reviewBox) {
      reviewBox.classList.add("hidden");
    }

    const reviewStatus =
      $("rreview");

    if (reviewStatus) {
      reviewStatus.textContent =
        "Recorded";
    }

    toast(
      "Human review recorded."
    );

    loadAudit();

  } catch (e) {

    toast(e.message);

  }
}


// ============================================================
// DASHBOARD
// ============================================================

function storeGrid(results) {

  if (!results || !results.length) {
    return "";
  }

  return (
    '<div class="store">' +
    '<span>Storage path</span>' +
    '<span>Status</span>' +
    '<span>Retrieval</span>' +
    '</div>' +

    results.map(r =>

      `<div class="store">
        <span>${esc(r.store_name)}</span>

        <span class="${
          r.result === "DELETED"
            ? "good"
            : "bad"
        }">
          ${esc(r.result)}
        </span>

        <span class="${
          r.retrievable
            ? "bad"
            : "good"
        }">
          ${
            r.retrievable
              ? "YES"
              : "NO"
          }
        </span>
      </div>`

    ).join("")
  );
}


async function dash() {

  try {

    const data =
      await api(
        "/api/dashboard"
      );

    if ($("total")) {
      $("total").textContent =
        data.total_memories;
    }

    if ($("verified")) {
      $("verified").textContent =
        data.verified_tests;
    }

    if ($("riskcount")) {
      $("riskcount").textContent =
        data.residual_risk_tests;
    }

    if ($("reviews")) {
      $("reviews").textContent =
        data.human_review_tests;
    }


    if (data.latest_verification) {

      const v =
        data.latest_verification;

      if ($("latestpill")) {

        $("latestpill").textContent =
          v.status === "VERIFIED"
            ? "VERIFIED"
            : "REVIEW";
      }

      if ($("latest")) {

        $("latest").innerHTML =

          `<div class="resultstats">

            <div>
              Memory
              <strong>
                ${esc(v.memory_id)}
              </strong>
            </div>

            <div>
              Completeness
              <strong>
                ${v.completeness}%
              </strong>
            </div>

            <div>
              Retrieval
              <strong class="${
                v.retrieval === "YES"
                  ? "bad"
                  : "good"
              }">
                ${esc(
                  retrievalText(
                    v.retrieval,
                    v.retrieval_label
                  )
                )}
              </strong>
            </div>

            <div>
              Risk
              <strong class="${
                v.risk === "LOW"
                  ? "good"
                  : v.risk === "MEDIUM"
                    ? "warn"
                    : "bad"
              }">
                ${esc(v.risk)}
              </strong>
            </div>

            <div>
              Residual stores
              <strong>
                ${esc(
                  v.residual_stores &&
                  v.residual_stores.length
                    ? v.residual_stores.join(", ")
                    : "None"
                )}
              </strong>
            </div>

            <div>
              Coverage
              <strong>
                ${esc(
                  v.coverage ||
                  (
                    (v.total_count || 0)
                    + "/5 stores checked"
                  )
                )}
              </strong>
            </div>

          </div>` +

          storeGrid(
            v.results
          );
      }
    }

  } catch (e) {

    const latest =
      $("latest");

    if (latest) {
      latest.textContent =
        e.message;
    }
  }
}


// ============================================================
// EVALUATION
// ============================================================

async function evalPage() {

  try {

    const data =
      await api(
        "/api/evaluation"
      );

    if ($("precision")) {

      $("precision").textContent =
        (
          data.precision * 100
        ).toFixed(1) + "%";
    }

    if ($("recall")) {

      $("recall").textContent =
        (
          data.recall * 100
        ).toFixed(1) + "%";
    }

    if ($("tp")) {
      $("tp").textContent =
        data.confusion_matrix.tp;
    }

    if ($("fp")) {
      $("fp").textContent =
        data.confusion_matrix.fp;
    }

    if ($("tn")) {
      $("tn").textContent =
        data.confusion_matrix.tn;
    }

    if ($("fn")) {
      $("fn").textContent =
        data.confusion_matrix.fn;
    }


    if ($("cases")) {

      $("cases").innerHTML =
        data.cases.map(c => {

          const ok =
            c.pass !== false &&
            (
              c.actual
                ? c.actual === c.expected
                : true
            );

          return `
            <div class="case">

              <b>
                ${esc(c.name)}
              </b>

              <br>

              Expected:
              ${esc(c.expected)}

              ·

              Actual:
              ${esc(c.actual || "—")}

              ·

              <span class="${
                ok
                  ? "good"
                  : "bad"
              }">
                ${ok ? "PASS" : "FAIL"}
              </span>

            </div>
          `;

        }).join("");
    }


    if ($("failures")) {

      $("failures").innerHTML =
        data.failure_cases
          .map(
            x =>
              `<div class="check">
                ${esc(x)}
              </div>`
          )
          .join("");
    }

  } catch (e) {

    if ($("cases")) {
      $("cases").textContent =
        e.message;
    }
  }
}


// ============================================================
// AUDIT TRAIL
// ============================================================

async function loadAudit() {

  try {

    const data =
      await api(
        "/api/audit"
      );

    const timeline =
      $("timeline");

    if (!timeline) return;

    timeline.innerHTML =
      data.length

        ? data.map(x =>

            `<div class="timeline-item">

              <div class="time">
                ${esc(x.timestamp)}
                ·
                ${esc(x.memory_id)}
              </div>

              <div class="event">
                ${esc(x.event)}
              </div>

              <div class="details">
                ${esc(x.details || "")}
              </div>

            </div>`

          ).join("")

        : '<div class="empty">No audit events yet.</div>';

  } catch (e) {

    const timeline =
      $("timeline");

    if (timeline) {
      timeline.textContent =
        e.message;
    }
  }
}


// ============================================================
// REPORT
// ============================================================

async function report() {

  if (!ver) {

    toast(
      "Run a verification first."
    );

    return;
  }

  try {

    const response =
      await fetch(
        "/api/reports/" +
        ver.verification.id +
        "/download",
        {
          method: "POST"
        }
      );

    if (!response.ok) {

      const data =
        await response
          .json()
          .catch(() => ({}));

      return toast(
        errDetail(
          data,
          response.status
        )
      );
    }

    const blob =
      await response.blob();

    const url =
      URL.createObjectURL(blob);

    const a =
      document.createElement("a");

    a.href = url;

    a.download =
      "HATAO_" +
      ver.verification.id +
      "_report.json";

    a.click();

    URL.revokeObjectURL(url);

    toast(
      "Report generated."
    );

  } catch (e) {

    toast(
      e.message ||
      "Report failed."
    );
  }
}


// ============================================================
// BUTTON CONNECTIONS
// ============================================================

const sendButton =
  document.querySelector(
    '[onclick*="send"]'
  );

if (sendButton) {
  sendButton.onclick = sendChat;
}


// Delete My Memory button
const deleteButton = $("del");

if (deleteButton) {
  deleteButton.onclick = del;
}

// ============================================================
// INITIAL LOAD
// ============================================================

dash();