# LinDangAgent

精简后的微信公众号后端项目，保留了：

- 微信公众号消息接入与验签
- 单股 AI 研报生成
- K 线预测与历史相似案例分析
- Top10 / Top100 查询与 Top100 复盘
- 研报落库与 HTML 展示

已剔除：

- Streamlit 页面与多页前端
- 炒股伙伴、玄学炒股、回测、对比页等前台功能

## 运行

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 80
```

## 关键目录

- `main.py`: FastAPI 与微信公众号入口
- `services/`: 研报、K 线预测、Top100 复盘
- `data/`: 股票数据与 K 线研究数据集
- `repositories/`: 研报存储
- `Stock_top10/`: 内嵌 Top10 引擎

## 配置

优先读取环境变量，也兼容 `.streamlit/secrets.toml`。
