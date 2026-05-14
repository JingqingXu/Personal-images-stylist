const analyzeForm = document.getElementById("analyzeForm");
const tryonForm = document.getElementById("tryonForm");

const userPhotoInput = document.getElementById("userPhoto");
const outfitTopInput = document.getElementById("outfitTop");
const outfitBottomInput = document.getElementById("outfitBottom");
const outfitShoesInput = document.getElementById("outfitShoes");
const sessionIdInput = document.getElementById("sessionId");
const sessionTokenInput = document.getElementById("sessionToken");

const analyzeBtn = document.getElementById("analyzeBtn");
const tryonBtn = document.getElementById("tryonBtn");

const userPreview = document.getElementById("userPreview");
const outfitTopPreview = document.getElementById("outfitTopPreview");
const outfitBottomPreview = document.getElementById("outfitBottomPreview");
const outfitShoesPreview = document.getElementById("outfitShoesPreview");

const advicePanel = document.getElementById("advicePanel");
const adviceText = document.getElementById("adviceText");
const adviceMeta = document.getElementById("adviceMeta");

const tryonPanel = document.getElementById("tryonPanel");
const resultImage = document.getElementById("resultImage");
const tryonMeta = document.getElementById("tryonMeta");

const statusBar = document.getElementById("statusBar");
const previewUrls = new Map();

function setStatus(message, isError = false) {
  statusBar.textContent = message;
  statusBar.classList.toggle("error", isError);
}

function clearPreviewUrl(key) {
  const oldUrl = previewUrls.get(key);
  if (oldUrl) {
    URL.revokeObjectURL(oldUrl);
    previewUrls.delete(key);
  }
}

function showPreview(fileInput, mountNode, key) {
  const file = fileInput.files?.[0];
  if (!file) {
    clearPreviewUrl(key);
    mountNode.innerHTML = "";
    mountNode.classList.add("hidden");
    return;
  }

  clearPreviewUrl(key);
  const objectUrl = URL.createObjectURL(file);
  previewUrls.set(key, objectUrl);
  mountNode.innerHTML = `<img src="${objectUrl}" alt="预览图" />`;
  mountNode.classList.remove("hidden");
}

async function parseJsonResponse(resp) {
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = data.error || `请求失败（${resp.status}）`;
    throw new Error(msg);
  }
  return data;
}

userPhotoInput.addEventListener("change", () => {
  showPreview(userPhotoInput, userPreview, "user");
});

outfitTopInput.addEventListener("change", () => {
  showPreview(outfitTopInput, outfitTopPreview, "outfit-top");
});

outfitBottomInput.addEventListener("change", () => {
  showPreview(outfitBottomInput, outfitBottomPreview, "outfit-bottom");
});

outfitShoesInput.addEventListener("change", () => {
  showPreview(outfitShoesInput, outfitShoesPreview, "outfit-shoes");
});

analyzeForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const userFile = userPhotoInput.files?.[0];
  if (!userFile) {
    setStatus("请先选择个人照片", true);
    return;
  }

  const formData = new FormData();
  formData.append("user_photo", userFile);

  analyzeBtn.disabled = true;
  setStatus("正在生成形象建议...");

  try {
    const resp = await fetch("/api/analyze", {
      method: "POST",
      body: formData,
    });
    const data = await parseJsonResponse(resp);
    if (!data.session_id || !data.session_token) {
      throw new Error("服务端未返回会话凭证，请重试");
    }

    sessionIdInput.value = data.session_id;
    sessionTokenInput.value = data.session_token;
    adviceText.textContent = data.advice || "";
    adviceMeta.textContent = `建议来源：${data.advice_provider || "unknown"}${
      data.note ? ` | ${data.note}` : ""
    }`;

    resultImage.src = "";
    tryonMeta.textContent = "";
    tryonPanel.classList.add("hidden");
    advicePanel.classList.remove("hidden");
    tryonBtn.disabled = false;
    setStatus("建议已生成，请上传服装图（上衣/裤子/鞋子可选，至少一张）进行换装");
  } catch (err) {
    sessionIdInput.value = "";
    sessionTokenInput.value = "";
    tryonBtn.disabled = true;
    setStatus(err.message || "生成建议失败", true);
  } finally {
    analyzeBtn.disabled = false;
  }
});

tryonForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const sessionId = sessionIdInput.value.trim();
  const sessionToken = sessionTokenInput.value.trim();
  if (!sessionId) {
    setStatus("请先完成步骤1，获取 session_id", true);
    return;
  }
  if (!sessionToken) {
    setStatus("会话凭证缺失，请重新执行步骤1", true);
    return;
  }

  const outfitTop = outfitTopInput.files?.[0];
  const outfitBottom = outfitBottomInput.files?.[0];
  const outfitShoes = outfitShoesInput.files?.[0];

  if (!outfitTop && !outfitBottom && !outfitShoes) {
    setStatus("请至少上传一张服装照片（上衣/裤子/鞋子）", true);
    return;
  }

  const formData = new FormData();
  formData.append("session_id", sessionId);
  formData.append("session_token", sessionToken);
  if (outfitTop) {
    formData.append("outfit_top", outfitTop);
  }
  if (outfitBottom) {
    formData.append("outfit_bottom", outfitBottom);
  }
  if (outfitShoes) {
    formData.append("outfit_shoes", outfitShoes);
  }

  tryonBtn.disabled = true;
  setStatus("正在调用换装模型生成结果...");

  try {
    const resp = await fetch("/api/try-on", {
      method: "POST",
      body: formData,
    });
    const data = await parseJsonResponse(resp);

    resultImage.src = data.result_photo_url;
    tryonMeta.textContent = `换装来源：${data.tryon_provider || "unknown"}${
      data.note ? ` | ${data.note}` : ""
    }`;

    tryonPanel.classList.remove("hidden");
    setStatus("换装完成");
  } catch (err) {
    setStatus(err.message || "换装失败", true);
  } finally {
    tryonBtn.disabled = false;
  }
});

window.addEventListener("beforeunload", () => {
  clearPreviewUrl("user");
  clearPreviewUrl("outfit-top");
  clearPreviewUrl("outfit-bottom");
  clearPreviewUrl("outfit-shoes");
});
