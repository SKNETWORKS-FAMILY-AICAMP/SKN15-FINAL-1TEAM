require("dotenv").config();
const express = require("express");
const cors = require("cors");
const fetch = require("node-fetch");  // 👈 추가
const app = express();
app.use(cors());
app.use(express.json());

app.listen(3000, () => console.log("✅ 서버 실행 중: http://localhost:3000"));

// 캡쳐 페이지 분석
app.post("/chat", async (req, res) => {
  const userMessage = req.body.message;

  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${process.env.OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: "gpt-4o",
      messages: [{ role: "user", content: userMessage }]
    })
  });

  const data = await response.json();
  res.json({ reply: data.choices[0].message.content });
});

// 캡쳐 페이지 이미지 설명 엔드포인트
app.post("/image", async (req, res) => {
  const { image } = req.body;

  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${process.env.OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: "gpt-4o-mini", // Vision 지원
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "이 이미지를 설명해줘" },
            { type: "image_url", image_url: { url: image } }
          ]
        }
      ]
    })
  });

  const data = await response.json();
  res.json({ reply: data.choices[0].message.content });
});



// pdf 분석
const pdfParse = require("pdf-parse");

app.post("/upload", async (req, res) => {
  const pdfBuffer = req.files.pdf.data;
  const data = await pdfParse(pdfBuffer);
  const text = data.text;

  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${process.env.OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: "gpt-4o",
      messages: [{ role: "user", content: `다음 PDF 내용을 요약해줘:\n\n${text}` }]
    })
  });

  const result = await response.json();
  res.json({ reply: result.choices[0].message.content });
});
