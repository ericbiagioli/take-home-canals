const ADDRESS = { line1: "350 5th Ave", city: "New York", state: "NY", postal_code: "10118", country: "US" };

const EXAMPLES = [
  {
    label: "Valid order",
    body: {
      customer_id: 1,
      shipping_address: ADDRESS,
      items: [{ product_id: 1, quantity: 2 }, { product_id: 4, quantity: 1 }],
      payment: { card_number: "4242424242424242", expiry_month: 12, expiry_year: 2030, cvv: "123" },
    },
  },
  {
    label: "Declined card",
    body: {
      customer_id: 1,
      shipping_address: ADDRESS,
      items: [{ product_id: 1, quantity: 1 }],
      payment: { card_number: "4000000000000002", expiry_month: 12, expiry_year: 2030, cvv: "123" },
    },
  },
  {
    label: "Customer not found",
    body: {
      customer_id: 999999999,
      shipping_address: ADDRESS,
      items: [{ product_id: 1, quantity: 1 }],
      payment: { card_number: "4242424242424242", expiry_month: 12, expiry_year: 2030, cvv: "123" },
    },
  },
  {
    label: "Product not found",
    body: {
      customer_id: 1,
      shipping_address: ADDRESS,
      items: [{ product_id: 999999999, quantity: 1 }],
      payment: { card_number: "4242424242424242", expiry_month: 12, expiry_year: 2030, cvv: "123" },
    },
  },
  {
    label: "Validation error",
    body: {
      customer_id: 1,
      shipping_address: { line1: "", city: "", state: "", postal_code: "", country: "" },
      items: [],
      payment: { card_number: "" },
    },
  },
];

const requestBody = document.getElementById("request-body");
const responseOutput = document.getElementById("response-output");
const sendStatus = document.getElementById("send-status");

function renderExamples() {
  const container = document.getElementById("examples");
  for (const example of EXAMPLES) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "example-button";
    button.textContent = example.label;
    button.addEventListener("click", () => {
      requestBody.value = JSON.stringify(example.body, null, 2);
    });
    container.appendChild(button);
  }
}

async function loadStats() {
  const response = await fetch("/api/dashboard/stats");
  const stats = await response.json();
  document.getElementById("stat-warehouses").textContent = stats.warehouses;
  document.getElementById("stat-customers").textContent = stats.customers;
  document.getElementById("stat-products").textContent = stats.products;
  document.getElementById("stat-orders").textContent = stats.orders;
}

async function loadHealth() {
  const badge = document.getElementById("db-health");
  try {
    const response = await fetch("/health");
    const data = await response.json();
    if (response.ok && data.status === "ok") {
      badge.textContent = "Database: healthy";
      badge.className = "health-badge health-ok";
    } else {
      badge.textContent = "Database: " + (data.detail || "unhealthy");
      badge.className = "health-badge health-error";
    }
  } catch (err) {
    badge.textContent = "Database: unreachable";
    badge.className = "health-badge health-error";
  }
}

function refreshAll() {
  loadStats();
  loadHealth();
}

async function sendRequest() {
  sendStatus.textContent = "Sending…";
  responseOutput.textContent = "—";

  let parsedBody;
  try {
    parsedBody = JSON.parse(requestBody.value);
  } catch (err) {
    sendStatus.textContent = "";
    responseOutput.textContent = "Invalid JSON: " + err.message;
    return;
  }

  try {
    const response = await fetch("/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parsedBody),
    });
    const data = await response.json();
    sendStatus.textContent = `HTTP ${response.status}`;
    responseOutput.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    sendStatus.textContent = "";
    responseOutput.textContent = "Request failed: " + err.message;
  } finally {
    refreshAll();
  }
}

async function runSmokeTests() {
  const button = document.getElementById("run-smoke-tests");
  const status = document.getElementById("smoke-status");
  const output = document.getElementById("smoke-output");

  button.disabled = true;
  status.textContent = "Running…";
  output.textContent = "—";

  try {
    const response = await fetch("/api/dashboard/smoke-test", { method: "POST" });
    const data = await response.json();
    status.textContent = data.success ? "All tests passed" : "Some tests failed";
    output.textContent = data.output || "(no output)";
  } catch (err) {
    status.textContent = "Failed to run tests";
    output.textContent = err.message;
  } finally {
    button.disabled = false;
    refreshAll();
  }
}

document.getElementById("send-request").addEventListener("click", sendRequest);
document.getElementById("run-smoke-tests").addEventListener("click", runSmokeTests);
document.getElementById("refresh-stats").addEventListener("click", refreshAll);

renderExamples();
refreshAll();
setInterval(loadHealth, 10000);
