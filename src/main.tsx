import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import Workspace from "./Workspace";
import "antd/dist/reset.css";
import "./styles.css";

const theme = {
  token: {
    colorPrimary: "#315de6",
    colorText: "#202b41",
    colorTextSecondary: "#718096",
    colorBorder: "#e3e8ef",
    colorBgLayout: "#f5f7fa",
    borderRadius: 7,
    fontSize: 13,
    controlHeight: 34,
    fontFamily:
      'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  components: {
    Table: {
      headerBg: "#f8f9fc",
      headerColor: "#6c778d",
      cellPaddingBlock: 17,
    },
    Menu: { itemHeight: 42, itemBorderRadius: 6 },
    Tabs: { horizontalItemGutter: 28 },
    Button: { primaryShadow: "none" },
  },
};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntApp>
        {location.protocol === "file:" ||
        new URLSearchParams(location.search).get("demo") === "1" ? (
          <App />
        ) : (
          <Workspace />
        )}
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
