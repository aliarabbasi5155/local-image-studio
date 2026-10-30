import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.cuda_memory import run_with_memory_retry


class OutOfMemory(RuntimeError):
    pass


class CUDAMemoryTests(unittest.TestCase):
    def memory(self):
        return SimpleNamespace(OOM_EXCEPTION=OutOfMemory, VRAMState=SimpleNamespace(NO_VRAM="offloaded"),
                               vram_state="low", EXTRA_RESERVED_VRAM=2*1024**3,
                               unload_all_models=Mock(), soft_empty_cache=Mock(),
                               throw_exception_if_processing_interrupted=Mock())

    def test_retries_same_operation_once_with_offloading_and_restores_settings(self):
        memory = self.memory()
        seen = []
        def operation():
            seen.append((memory.vram_state, memory.EXTRA_RESERVED_VRAM))
            if len(seen) == 1:
                raise OutOfMemory("activation")
            return "same seed and same dimensions"
        self.assertEqual(run_with_memory_retry(operation, memory), "same seed and same dimensions")
        self.assertEqual(seen, [("low",2*1024**3),("offloaded",3*1024**3)])
        self.assertEqual((memory.vram_state,memory.EXTRA_RESERVED_VRAM),("low",2*1024**3))
        memory.unload_all_models.assert_called_once()

    def test_second_oom_is_propagated_and_settings_are_restored(self):
        memory = self.memory()
        operation = Mock(side_effect=OutOfMemory("too large"))
        with self.assertRaises(OutOfMemory):
            run_with_memory_retry(operation, memory)
        self.assertEqual(operation.call_count,2)
        self.assertEqual(memory.vram_state,"low")

    def test_success_and_unrelated_errors_are_not_retried(self):
        memory = self.memory()
        self.assertEqual(run_with_memory_retry(lambda: 42,memory),42)
        operation = Mock(side_effect=ValueError("bad model"))
        with self.assertRaises(ValueError):
            run_with_memory_retry(operation,memory)
        operation.assert_called_once()
        memory.unload_all_models.assert_not_called()
