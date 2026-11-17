# FP-01 UI 修复候选入库

目标：交付 UI-01/UI-02 修复源码供独立审查。负责人 FP-01；涉及 frontend/workbench、候选归档、README 与自动测试步骤。无后端适配、无部署。

## 版本与可复现性

- 候选：FP01-UI-FIX-20261117；原 FP01-140 在本机交接存档保留，首轮失败证据在 PR #2。
- `frontend/workbench/` 的全部 13 个文件逐字节匹配群 #153 候选原始 JSON；没有在入库时修改应用或测试。
- 原始 JSON：`docs/handoffs/fp01-ui-fix-20261117/fp01-ui-fix-sources.json`。
- 原始 JSON SHA256：`023105b4abe584059d3c4bade78fb930245320bfe882859fc11d97ced0f77e04`。
- JSON 含逐文件 SHA；工作台的 SHA256SUMS.json 列出其余 12 个文件。还原脚本放在原始 JSON 旁；只用于新目录，还原不是运行工作台的必要步骤。

## 修复及验收边界

UI-01：申请审批和确认分类前比较可见字段、案例名称及询价原文。未保存时提示并保留输入，不静默锁定旧值；保存后必须重新确认分类。

UI-02：箱数完整数值片段只接受正安全整数；小数、负数、混合标点、多值或范围保留原文，箱数留空并阻断试算。

开发自动化：14 项通过。新增动作函数测试使用 DOM/FormData 替身，不是真实浏览器测试；对未改 FP01-140 运行新回归能复现两项失败。

独立 FP-03 回报（群 #157/#159）：Windows Edge 单标签合成环境中，两项 P1、正常 1260 CNY 流程、重复点击与拒绝后刷新通过。固定验收提交 `8c218d51b78993418e167152f0f78b746e0a14ff`，报告：
https://github.com/3258055792-lab/freightpilot-ai/blob/8c218d51b78993418e167152f0f78b746e0a14ff/docs/qa/browser-retest-2026-11-17.md

验收报告在 PR #2；本次是独立源码交付，不修改他人验收分支。FP-04 静态复核仍待完成。真实后端、第二来源切换、真实 Excel 映射、订单和发布未据此验收。

启动见根 README；应用 JS 读取同源合成 JSON，CSS 还请求 Google Fonts。原工作台中的订单等入口不属于本轮验收范围。

## Windows 检出格式

GitHub Windows CI 首轮将源码检出为 CRLF，导致动作测试的首行 import 去除规则失配。`.gitattributes` 固定工作台及原始 JSON 归档为 LF；应用和测试字节不变，同时保证 Windows 检出的文件仍符合候选 SHA。
