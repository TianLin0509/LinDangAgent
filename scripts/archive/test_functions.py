#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试主要功能模块"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

print("=" * 60)
print("LinDangAgent 功能测试")
print("=" * 60)

# 1. 测试主项目 AI 客户端
print("\n[1] 测试主项目 AI 客户端初始化...")
try:
    from ai.client import get_ai_client
    from config import MODEL_NAMES
    client, cfg, err = get_ai_client(MODEL_NAMES[0])
    if err and "API Key" in err:
        print("  ✅ AI 客户端正常（需配置 API Key）")
    elif err:
        print(f"  ❌ AI 客户端异常: {err}")
    else:
        print("  ✅ AI 客户端初始化成功")
except Exception as e:
    print(f"  ❌ 导入失败: {e}")

# 2. 测试 Tushare 数据客户端
print("\n[2] 测试 Tushare 数据客户端...")
try:
    from data.tushare_client import get_tushare_client
    ts = get_tushare_client()
    if ts:
        print("  ✅ Tushare 客户端正常")
    else:
        print("  ⚠️  Tushare 未配置（非关键）")
except Exception as e:
    print(f"  ❌ 异常: {e}")

# 3. 测试 Top10 模块导入
print("\n[3] 测试 Top10 模块...")
try:
    from services import rank_service
    from pathlib import Path
    top10_dir = rank_service.get_top10_repo_dir(Path(__file__).parent)
    rank_service.ensure_top10_import_path(top10_dir)
    from top10.deep_runner import get_deep_status
    status = get_deep_status()
    print(f"  ✅ Top10 模块正常，状态: {status.get('status') if status else '无'}")
except Exception as e:
    print(f"  ❌ 异常: {e}")

# 5. 测试存储服务
print("\n[5] 测试存储服务...")
try:
    from storage.report_store import ReportStore
    store = ReportStore()
    print("  ✅ 存储服务正常")
except Exception as e:
    print(f"  ❌ 异常: {e}")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
