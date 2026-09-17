let mem = null;
let ver = null;

const $ = id => document.getElementById(id);

const STORE_MAP = {
  'Original Memory': 'original',
  'Summary Store': 'summary',
  'Embedding Store': 'embedding',
  'Cache': 'cache',
  'Downstream Copy': 'downstream'
};


/* =========================
   API
========================= */

function errDetail(d, status) {

  let msg = d && d.detail;

  if (Array.isArray(msg)) {
    msg = msg
      .map(x => x.msg || JSON.stringify(x))
      .join('; ');
  }

  if (typeof msg === 'object' && msg) {
    msg = msg.msg || JSON.stringify(msg);
  }

  return msg || ('Request failed (' + status + ')');
}


async function api(url, options = {}) {

  let response;
  let data;

  try {
    response = await fetch(url, options);
  } catch (error) {
    throw Error(
      'Cannot reach the AI39 server. Confirm the backend is running.'
    );
  }

  data = await response
    .json()
    .catch(() => ({}));

  if (!response.ok) {
    throw Error(errDetail(data, response.status));
  }

  return data;
}


/* =========================
   UI HELPERS
========================= */

function toast(message) {

  const element = $('toast');

  element.textContent = message;
  element.classList.add('show');

  setTimeout(() => {
    element.classList.remove('show');
  }, 2800);
}


function page(pageName) {

  document
    .querySelectorAll('.page')
    .forEach(x => x.classList.remove('active'));

  document
    .querySelectorAll('.nav')
    .forEach(x => x.classList.remove('active'));

  const target = $(pageName);

  if (target) {
    target.classList.add('active');
  }

  const nav = document.querySelector(
    `[data-page="${pageName}"]`
  );

  if (nav) {
    nav.classList.add('active');
  }

  if (pageName === 'dashboard') {
    dash();
  }

  if (pageName === 'evaluation') {
    evalPage();
  }

  if (pageName === 'audit') {
    loadAudit();
  }
}


document
  .querySelectorAll('.nav')
  .forEach(button => {

    button.onclick = () => {
      page(button.dataset.page);
    };

  });


function esc(value) {

  return String(value ?? '')
    .replace(/[&<>"']/g, character => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[character]));

}


/* =========================
   CHATBOT
========================= */

function addMessage(text, type = 'bot') {

  const log = $('log');

  const wrapper = document.createElement('div');

  wrapper.className =
    'message ' +
    (type === 'user'
      ? 'user-message'
      : 'bot-message');


  const avatar = document.createElement('div');

  avatar.className = 'avatar';

  avatar.textContent =
    type === 'user'
      ? 'YOU'
      : 'AI';


  const bubble = document.createElement('div');

  bubble.className =
    'bubble ' +
    (type === 'user'
      ? 'user'
      : 'bot');

  bubble.textContent = text;


  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);

  log.appendChild(wrapper);

  log.scrollTop = log.scrollHeight;
}


function botMessage(text) {
  addMessage(text, 'bot');
}


function userMessage(text) {
  addMessage(text, 'user');
}


function demo(text) {

  $('text').value = text;
  $('text').focus();

}


function handleChatKey(event) {

  if (
    event.key === 'Enter' &&
    !event.shiftKey
  ) {

    event.preventDefault();

    sendMessage();

  }

}


/*
  This is intentionally a simulated chatbot.
  No external LLM API is used.
*/

async function sendMessage() {

  const input = $('text');

  const message = input.value.trim();

  if (!message) {
    return;
  }

  userMessage(message);

  input.value = '';


  const lower = message.toLowerCase();


  /* =========================
     DELETE COMMAND
  ========================= */

  if (
    lower.includes('delete my memory') ||
    lower.includes('delete memory') ||
    lower.includes('forget my memory') ||
    lower.includes('remove my memory')
  ) {

    if (!mem) {

      botMessage(
        'I do not currently have a stored synthetic memory to delete. Store a test memory first.'
      );

      return;
    }


    botMessage(
      `Deletion requested for memory ${mem.id}. Running verification across 5 simulated storage paths...`
    );

    await deleteMemory();

    return;
  }


  /* =========================
     RETRIEVAL COMMAND
  ========================= */

  if (
    lower.includes('what is my') ||
    lower.includes('what was my') ||
    lower.includes('remember') ||
    lower.includes('retrieve') ||
    lower.includes('recall')
  ) {

    if (!mem) {

      botMessage(
        'I do not have a stored synthetic memory yet. Tell me something first.'
      );

      return;
    }


    try {

      const data = await api(
        '/api/memories/' + mem.id
      );


      if (
        data.memory &&
        data.memory.content
      ) {

        botMessage(
          `Your stored synthetic memory contains: "${data.memory.content}"`
        );

      } else {

        botMessage(
          'The synthetic memory is no longer available through the current memory path.'
        );

      }

    } catch (error) {

      botMessage(
        'I could not retrieve the synthetic memory from the test environment.'
      );

    }

    return;
  }


  /* =========================
     STORE MEMORY
  ========================= */

  try {

    const data = await api(
      '/api/memories',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          content: message
        })
      }
    );


    renderMemory(data);

    botMessage(
      `Got it. I've stored that as synthetic memory ${data.memory.id} and propagated it across 5 simulated memory paths.`
    );


    botMessage(
      `You can now ask me to retrieve it or say "Delete my memory" to start deletion verification.`
    );


    toast('Synthetic memory created.');

    dash();

  } catch (error) {

    botMessage(
      'I could not create the synthetic memory: ' +
      error.message
    );

    toast(error.message);

  }

}


/* =========================
   MEMORY STORAGE
========================= */

async function store() {

  const text = $('text').value.trim();

  if (!text) {

    toast(
      'Enter synthetic test information first.'
    );

    return;
  }

  await sendMessage();

}


/* =========================
   IMAGE UPLOAD
========================= */

async function upload() {

  const file = $('img').files[0];

  if (!file) {
    return;
  }


  if (file.size > 5 * 1024 * 1024) {

    toast(
      'Image is larger than 5 MB.'
    );

    $('img').value = '';

    return;
  }


  const formData = new FormData();

  formData.append(
    'file',
    file
  );


  try {

    const data = await api(
      '/api/memories/image',
      {
        method: 'POST',
        body: formData
      }
    );


    renderMemory(data);


    userMessage(
      `Test image: ${file.name}`
    );


    botMessage(
      `Test image stored as synthetic memory ${data.memory.id}. It has been propagated across 5 simulated memory paths.`
    );


    botMessage(
      'You can now request deletion verification for this memory.'
    );


    toast('Test image stored.');

    dash();

  } catch (error) {

    botMessage(
      'I could not store the test image: ' +
      error.message
    );

    toast(error.message);

  }


  $('img').value = '';

}


/* =========================
   MEMORY RENDERING
========================= */

function renderMemory(data) {

  if (!data || !data.memory) {
    return;
  }


  mem = data.memory;


  $('mid').textContent =
    mem.id || '—';


  $('memoryStatus').textContent =
    `Memory ${mem.id} active`;


  $('del').disabled = false;


  if (data.stores) {

    data.stores.forEach(store => {

      const element =
        $('n-' + STORE_MAP[store.store_name]);

      if (element) {

        element.textContent =
          store.present
            ? 'ACTIVE'
            : 'DELETED';

        element.className =
          store.present
            ? ''
            : 'deleted-status';

      }

    });

  }

}


/* =========================
   DELETE MEMORY
========================= */

async function deleteMemory() {

  if (!mem) {

    toast(
      'No synthetic memory is currently stored.'
    );

    return;
  }


  $('del').disabled = true;


  try {

    const data = await api(
      '/api/deletion-requests/' + mem.id,
      {
        method: 'POST'
      }
    );


    toast(
      'Deletion request issued. Running verification…'
    );


    ver =
      data.verification ||
      await api(
        '/api/verifications/' + mem.id,
        {
          method: 'POST'
        }
      );


    const memoryState =
      data.memory ||
      await api(
        '/api/memories/' + mem.id
      );


    renderMemory(memoryState);

    renderVerification(ver);


    $('result')
      .classList
      .remove('hidden');


    if (
      ver.verification &&
      ver.verification.status === 'VERIFIED'
    ) {

      botMessage(
        `Deletion verification completed. AI39 checked all 5 simulated storage paths and found no retrievable residual memory.`
      );

    } else {

      const residuals =
        ver.results
          ? ver.results
              .filter(x => x.result === 'FOUND')
              .map(x => x.store_name)
          : [];


      botMessage(
        `Deletion verification completed. Residual synthetic memory was detected in: ${
          residuals.length
            ? residuals.join(', ')
            : 'one or more simulated paths'
        }. Human review is required.`
      );

    }


    await dash();

    toast(
      'Verification complete.'
    );


  } catch (error) {

    $('del').disabled = false;

    botMessage(
      'The deletion verification could not be completed: ' +
      error.message
    );

    toast(error.message);

  }

}


/* Keep old function name compatible with any existing HTML. */
async function del() {
  await deleteMemory();
}


/* =========================
   VERIFICATION
========================= */

function retrievalText(
  code,
  label
) {

  if (label) {
    return label;
  }

  return code === 'YES'
    ? 'YES — Memory is still retrievable'
    : 'NO — Memory cannot be retrieved';

}


function renderVerification(data) {

  const verification =
    data.verification || {};

  const results =
    data.results || [];


  const residuals =
    results.filter(
      result => result.result === 'FOUND'
    );


  const retrievalCode =
    verification.retrieval;


  $('score').textContent =
    verification.completeness + '%';


  $('retrieval').textContent =
    retrievalText(
      retrievalCode,
      verification.retrieval_label ||
      data.retrieval_label
    );


  $('retrieval').className =
    retrievalCode === 'YES'
      ? 'bad'
      : 'good';


  $('rrisk').textContent =
    verification.risk;


  $('rrisk').className =
    verification.risk === 'LOW'
      ? 'good'
      : verification.risk === 'MEDIUM'
        ? 'warn'
        : 'bad';


  $('rcount').textContent =
    residuals.length
      ? residuals
          .map(x => x.store_name)
          .join(', ')
      : 'None';


  $('rcover').textContent =
    verification.coverage ||
    data.coverage ||
    (
      (verification.total_count ||
       results.length) +
      '/5 stores checked'
    );


  $('rreview').textContent =
    verification.status === 'VERIFIED'
      ? 'Not required'
      : 'Required';


  $('rreview').className =
    verification.status === 'VERIFIED'
      ? 'good'
      : 'warn';


  $('rtitle').textContent =
    verification.status === 'VERIFIED'
      ? 'Deletion verified'
      : 'Residual memory detected';


  /* =========================
     STORAGE TABLE
  ========================= */

  $('stores').innerHTML =
    '<div class="store">' +
      '<span>Storage path</span>' +
      '<span>Status</span>' +
      '<span>Retrieval</span>' +
    '</div>' +

    results.map(result => {

      const deleted =
        result.result === 'DELETED';

      return `
        <div class="store">
          <span>${esc(result.store_name)}</span>

          <span class="${deleted ? 'good' : 'bad'}">
            ${deleted ? '✓ DELETED' : '✕ FOUND'}
          </span>

          <span class="${result.retrievable ? 'bad' : 'good'}">
            ${result.retrievable ? 'YES' : 'NO'}
          </span>
        </div>
      `;

    }).join('');


  /* =========================
     EVIDENCE
  ========================= */

  if (residuals.length) {

    $('evidence').innerHTML =
      '<b>Residual Risk & Evidence</b><br>' +

      'Residual memory detected in <b>' +

      residuals
        .map(x => esc(x.store_name))
        .join(', ') +

      '</b>.<br><br>' +

      residuals
        .map(x =>
          `• ${esc(x.store_name)} — confidence ${
            (x.confidence * 100).toFixed(0)
          }% — ${esc(x.evidence)}`
        )
        .join('<br>') +

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
        <b>${esc(verification.risk)}</b>
      `;


    $('finding').innerHTML =
      '<b>Residual stores:</b> ' +

      residuals
        .map(x => esc(x.store_name))
        .join(', ') +

      '<br><b>Evidence:</b><br>' +

      residuals
        .map(x =>
          `• ${esc(x.evidence)}`
        )
        .join('<br>');


  } else {

    $('evidence').innerHTML =
      '<b>Evidence</b><br>' +
      'No retrievable synthetic memory was found ' +
      'in any simulated store.';


    $('finding').innerHTML = '';

  }


  $('reviewbox')
    .classList
    .toggle(
      'hidden',
      verification.status === 'VERIFIED'
    );

}


/* Keep compatibility with previous function name. */
function renderVer(data) {
  renderVerification(data);
}


/* =========================
   FAILURE SCENARIOS
========================= */

async function scenario(name) {

  try {

    const data =
      await api(
        '/api/demo/scenario/' + name,
        {
          method: 'POST'
        }
      );


    renderMemory(data.memory);

    ver =
      data.verification;


    renderVerification(ver);


    $('result')
      .classList
      .remove('hidden');


    page('chat');


    botMessage(
      `Synthetic failure scenario "${name}" has been executed.`
    );


    const residuals =
      ver.results
        ? ver.results.filter(
            x => x.result === 'FOUND'
          )
        : [];


    if (residuals.length) {

      botMessage(
        `AI39 detected residual memory in: ${
          residuals
            .map(x => x.store_name)
            .join(', ')
        }.`
      );

    } else {

      botMessage(
        'AI39 found no residual synthetic memory across the simulated storage paths.'
      );

    }


    await dash();

    toast(
      name + ' scenario verified.'
    );


  } catch (error) {

    toast(error.message);

  }

}


/* =========================
   HUMAN REVIEW
========================= */

async function submitReview() {

  if (!ver) {

    toast(
      'Run a verification first.'
    );

    return;
  }


  const reviewer =
    $('reviewer').value.trim();


  if (!reviewer) {

    toast(
      'Enter reviewer name.'
    );

    return;
  }


  try {

    await api(
      '/api/reviews',
      {
        method: 'POST',

        headers: {
          'Content-Type':
            'application/json'
        },

        body: JSON.stringify({

          verification_id:
            ver.verification.id,

          reviewer,

          decision:
            $('decision').value,

          comments:
            $('comments').value

        })
      }
    );


    $('reviewbox')
      .classList
      .add('hidden');


    $('rreview').textContent =
      'Recorded';


    $('rreview').className =
      'good';


    toast(
      'Human review recorded.'
    );


    loadAudit();

  } catch (error) {

    toast(error.message);

  }

}


/* =========================
   DASHBOARD
========================= */

function storeGrid(results) {

  if (
    !results ||
    !results.length
  ) {

    return '';

  }


  return (

    '<div class="store">' +
      '<span>Storage path</span>' +
      '<span>Status</span>' +
      '<span>Retrieval</span>' +
    '</div>' +

    results.map(result => `

      <div class="store">

        <span>
          ${esc(result.store_name)}
        </span>

        <span class="${
          result.result === 'DELETED'
            ? 'good'
            : 'bad'
        }">

          ${esc(result.result)}

        </span>

        <span class="${
          result.retrievable
            ? 'bad'
            : 'good'
        }">

          ${
            result.retrievable
              ? 'YES'
              : 'NO'
          }

        </span>

      </div>

    `).join('')

  );

}


async function dash() {

  try {

    const data =
      await api('/api/dashboard');


    $('total').textContent =
      data.total_memories;


    $('verified').textContent =
      data.verified_tests;


    $('riskcount').textContent =
      data.residual_risk_tests;


    $('reviews').textContent =
      data.human_review_tests;


    if (data.latest_verification) {

      const verification =
        data.latest_verification;


      $('latestpill').textContent =
        verification.status === 'VERIFIED'
          ? 'VERIFIED'
          : 'REVIEW';


      $('latest').innerHTML = `

        <div class="resultstats">

          <div>
            Memory
            <strong>
              ${esc(verification.memory_id)}
            </strong>
          </div>

          <div>
            Completeness
            <strong>
              ${verification.completeness}%
            </strong>
          </div>

          <div>
            Retrieval
            <strong class="${
              verification.retrieval === 'YES'
                ? 'bad'
                : 'good'
            }">

              ${esc(
                retrievalText(
                  verification.retrieval,
                  verification.retrieval_label
                )
              )}

            </strong>
          </div>

          <div>
            Risk

            <strong class="${
              verification.risk === 'LOW'
                ? 'good'
                : verification.risk === 'MEDIUM'
                  ? 'warn'
                  : 'bad'
            }">

              ${esc(
                verification.risk
              )}

            </strong>

          </div>

          <div>
            Residual stores

            <strong>

              ${esc(
                verification.residual_stores &&
                verification.residual_stores.length
                  ? verification.residual_stores.join(', ')
                  : 'None'
              )}

            </strong>

          </div>

          <div>
            Coverage

            <strong>

              ${esc(
                verification.coverage ||
                (
                  (verification.total_count || 0) +
                  '/5 stores checked'
                )
              )}

            </strong>

          </div>

        </div>

      ` +

      storeGrid(
        verification.results
      );

    }

  } catch (error) {

    $('latest').textContent =
      error.message;

  }

}


/* =========================
   EVALUATION
========================= */

async function evalPage() {

  try {

    const data =
      await api('/api/evaluation');


    $('precision').textContent =
      (data.precision * 100)
        .toFixed(1) + '%';


    $('recall').textContent =
      (data.recall * 100)
        .toFixed(1) + '%';


    $('tp').textContent =
      data.confusion_matrix.tp;


    $('fp').textContent =
      data.confusion_matrix.fp;


    $('tn').textContent =
      data.confusion_matrix.tn;


    $('fn').textContent =
      data.confusion_matrix.fn;


    $('cases').innerHTML =
      data.cases.map(testCase => {

        const passed =
          testCase.pass !== false &&
          (
            testCase.actual
              ? testCase.actual ===
                testCase.expected
              : true
          );


        return `

          <div class="case">

            <b>
              ${esc(testCase.name)}
            </b>

            <br>

            Expected:
            ${esc(testCase.expected)}

            ·

            Actual:
            ${esc(testCase.actual || '—')}

            ·

            <span class="${
              passed
                ? 'good'
                : 'bad'
            }">

              ${
                passed
                  ? 'PASS'
                  : 'FAIL'
              }

            </span>

          </div>

        `;

      }).join('');


    $('failures').innerHTML =
      data.failure_cases
        .map(item =>
          `<div class="check">
            ${esc(item)}
          </div>`
        )
        .join('');


  } catch (error) {

    $('cases').textContent =
      error.message;

  }

}


/* =========================
   AUDIT TRAIL
========================= */

async function loadAudit() {

  try {

    const data =
      await api('/api/audit');


    $('timeline').innerHTML =
      data.length

        ? data.map(item => `

            <div class="timeline-item">

              <div class="time">
                ${esc(item.timestamp)}
                ·
                ${esc(item.memory_id)}
              </div>

              <div class="event">
                ${esc(item.event)}
              </div>

              <div class="details">
                ${esc(item.details || '')}
              </div>

            </div>

          `).join('')

        : `
          <div class="empty">
            No audit events yet.
          </div>
        `;


  } catch (error) {

    $('timeline').textContent =
      error.message;

  }

}


/* =========================
   REPORT
========================= */

async function report() {

  if (!ver) {

    toast(
      'Run a verification first.'
    );

    return;
  }


  try {

    const response =
      await fetch(
        '/api/reports/' +
        ver.verification.id +
        '/download',
        {
          method: 'POST'
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


    const anchor =
      document.createElement('a');


    anchor.href = url;


    anchor.download =
      'AI39_' +
      ver.verification.id +
      '_report.json';


    document.body.appendChild(anchor);

    anchor.click();

    anchor.remove();


    URL.revokeObjectURL(url);


    toast(
      'Report generated.'
    );


  } catch (error) {

    toast(
      error.message ||
      'Report failed.'
    );

  }

}


/* =========================
   STARTUP
========================= */

dash();