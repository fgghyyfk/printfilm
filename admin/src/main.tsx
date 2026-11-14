import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import App from "./App";
import "./index.css";
import "./styles/admin-console.css";
import "./styles/admin-forms.css";

// 路由前缀跟随构建 base（如 /admin/），根路径部署时为空
const basename = import.meta.env.BASE_URL.replace(/\/$/, "");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename={basename || undefined}>
      <App />
      <Toaster richColors position="top-right" />
    </BrowserRouter>
  </StrictMode>,
);
