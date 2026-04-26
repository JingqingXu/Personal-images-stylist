const analyzeForm = document.getElementById("analyzeForm");
const tryonForm = document.getElementById("tryonForm");

const userPhotoInput = document.getElementById("userPhoto");
const outfitPhotoInput = document.getElementById("outfitPhoto");
const sessionIdInput = document.getElementById("sessionId");

const analyzeBtn = document.getElementById("analyzeBtn");
const tryonBtn = document.getElementById("tryonBtn");

const userPreview = document.getElementById("userPreview");
const outfitPreview = document.getElementById("outfitPreview");

const advicePanel = document.getElementById("advicePanel");
const adviceText = document.getElementById("adviceText");
const adviceMeta = document.getElementById("adviceMeta");

const tryonPanel = document.getElementById("tryonPanel");
const resultImage = document.getElementById("resultImage");
const tryonMeta = document.getElementById("tryonMeta");

const statusBar = document.getElementById("statusBar");

function setStatus(message, isError = false) {
  statusBar.textContent = message;
  statusBar.classList.toggle("error", isError);
}

function showPreview(fileInput, mountNode) {
  const file = fileInput.files?.[0];
  if (!file) {
    mountNode.innerHTML = "";
    mountNode.classList.add("hidden");
    return;
  }

  const objectUrl = URL.createObjectURL(file);
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
  showPreview(userPhotoInput, userPreview);
});

outfitPhotoInput.addEventListener("change", () => {
  showPreview(outfitPhotoInput, outfitPreview);
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
  setStatus("正在调用千问生成形象建议...");

  try {
    const resp = await fetch("/api/analyze", {
      method: "POST",
      body: formData,
    });
    const data = await parseJsonResponse(resp);

    sessionIdInput.value = data.session_id;
    adviceText.textContent = data.advice || "";
    adviceMeta.textContent = `建议来源：${data.advice_provider || "unknown"}${
      data.note ? ` | ${data.note}` : ""
    }`;

    advicePanel.classList.remove("hidden");
    tryonBtn.disabled = false;
    setStatus("建议已生成，请上传服装照片进行换装");
  } catch (err) {
    setStatus(err.message || "生成建议失败", true);
  } finally {
    analyzeBtn.disabled = false;
  }
});

tryonForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const sessionId = sessionIdInput.value.trim();
  if (!sessionId) {
    setStatus("请先完成步骤1，获取 session_id", true);
    return;
  }

  const outfitFile = outfitPhotoInput.files?.[0];
  if (!outfitFile) {
    setStatus("请先选择服装照片", true);
    return;
  }

  const formData = new FormData();
  formData.append("session_id", sessionId);
  formData.append("outfit_photo", outfitFile);

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
