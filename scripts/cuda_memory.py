"""Retry a CUDA stage once with weights offloaded, preserving its inputs and seed."""
import gc
import logging


def run_with_memory_retry(operation, memory):
    try:
        return operation()
    except memory.OOM_EXCEPTION:
        logging.warning("GPU working memory exhausted; retrying this stage with full weight offloading.")
    # Leave the except block before collecting, so its traceback cannot retain tensors.
    memory.unload_all_models()
    gc.collect()
    memory.soft_empty_cache()
    memory.throw_exception_if_processing_interrupted()
    previous_state, previous_reserve = memory.vram_state, memory.EXTRA_RESERVED_VRAM
    try:
        memory.vram_state = memory.VRAMState.NO_VRAM
        memory.EXTRA_RESERVED_VRAM = max(previous_reserve, 3 * 1024**3)
        return operation()
    finally:
        memory.vram_state, memory.EXTRA_RESERVED_VRAM = previous_state, previous_reserve
