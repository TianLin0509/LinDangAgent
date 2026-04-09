#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

from ai.client import get_ai_client
from config import MODEL_NAMES

print("测试 AI 客户端初始化...")
for i, model_name in enumerate(MODEL_NAMES[:3]):
    client, cfg, err = get_ai_client(model_name)
    status = "OK" if client else f"FAIL: {err}"
    print(f"{i+1}. {model_name} -> {status}")
