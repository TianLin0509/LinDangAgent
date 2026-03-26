"""兼容装饰器 — 在 Streamlit 上下文中使用 st.cache_data，后台线程中直接调用"""

import functools
import logging

logger = logging.getLogger(__name__)


def _has_streamlit_context() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def compat_cache(ttl: int = 1800, show_spinner: bool = False):
    """替代 @st.cache_data 的兼容装饰器。
    - 有 Streamlit 上下文时走 st.cache_data
    - 无上下文时（后台线程）直接调用原函数
    """
    def decorator(fn):
        # 预先创建 st.cache_data 版本
        _cached_fn = None

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            nonlocal _cached_fn
            if _has_streamlit_context():
                if _cached_fn is None:
                    import streamlit as st
                    _cached_fn = st.cache_data(ttl=ttl, show_spinner=show_spinner)(fn)
                return _cached_fn(*args, **kwargs)
            return fn(*args, **kwargs)

        # 保留原始函数引用，方便直接调用
        wrapper._original = fn
        return wrapper

    return decorator
