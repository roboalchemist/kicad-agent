/** Layer panel walkthrough: show/hide and per-layer translucency, both persisted.
 *  Run with `node e2e/layers.mjs` against a served build (BASE_URL, default the Vite dev server). */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const SHOTS = new URL("./screenshots/", import.meta.url).pathname;
mkdirSync(SHOTS, { recursive: true });
const BASE = process.env.BASE_URL ?? "http://127.0.0.1:5173";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text().slice(0, 300)));

const check = (ok, message) => {
  if (!ok) throw new Error(message);
  console.log(`ok  ${message}`);
};

await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.waitForSelector("[data-testid=layer-panel]", { timeout: 30000 });
await page.waitForSelector("[data-testid='board-layer-F.Cu']");

const rows = await page.locator("[data-testid=layer-panel] .layer-row").count();
const sliders = await page.locator("[data-testid=layer-panel] .layer-alpha").count();
check(rows > 0 && rows === sliders, `every layer row has an opacity slider (${sliders})`);

const opacityOf = (layer) =>
  page.locator(`[data-testid='board-layer-${layer}']`).evaluate((el) => getComputedStyle(el).opacity);

const slider = page.locator("[data-testid='layer-alpha-F.Cu']");
check((await slider.inputValue()) === "100", "a layer starts fully opaque");

await slider.fill("40");
await page.waitForTimeout(150);
check((await opacityOf("F.Cu")) === "0.4", "the slider makes the layer translucent in the view");
check((await page.locator("[data-testid='layer-alpha-value-F.Cu']").textContent()) === "40%",
  "the row shows the percentage");

await page.locator("[data-testid='layer-toggle-F.Cu']").click();
await page.waitForTimeout(150);
check((await page.locator("[data-testid='board-layer-F.Cu']").count()) === 0,
  "unchecking hides the layer");
check(await slider.isDisabled(), "a hidden layer's opacity slider is disabled");
await page.locator("[data-testid='layer-toggle-F.Cu']").click();

await page.locator("[data-testid='layers-reset-alpha']").click();
await page.waitForTimeout(150);
check((await opacityOf("F.Cu")) === "1", "the opaque button restores full opacity");

await slider.fill("25");
await page.waitForTimeout(150);
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector("[data-testid=layer-panel]");
check((await opacityOf("F.Cu")) === "0.25", "translucency survives a reload");
check((await page.locator("[data-testid='layer-alpha-F.Cu']").inputValue()) === "25",
  "the slider restores its saved position");

await page.screenshot({ path: `${SHOTS}30-layer-opacity.png` });
check(errors.length === 0, `no console errors (${errors.slice(0, 2).join("; ")})`);
await browser.close();
