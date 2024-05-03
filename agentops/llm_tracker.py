import functools
from .log_config import logger
from .event import LLMEvent, ErrorEvent
from .helpers import get_ISO_time, check_call_stack_for_agent_id
import inspect
import pprint


class LlmTracker:
    def __init__(self, client):
        self.client = client

    def _handle_response_v0_openai(self, response, kwargs, init_timestamp):
        """Handle responses for OpenAI versions <v1.0.0"""

        self.llm_event = LLMEvent(
            init_timestamp=init_timestamp,
            params=kwargs
        )

        def handle_stream_chunk(chunk):
            # NOTE: prompt/completion usage not returned in response when streaming
            # We take the first ChatCompletionChunk and accumulate the deltas from all subsequent chunks to build one full chat completion
            if self.llm_event.returns == None:
                self.llm_event.returns = chunk

            try:
                accumulated_delta = self.llm_event.returns['choices'][0]['delta']
                self.llm_event.agent_id = check_call_stack_for_agent_id()
                self.llm_event.model = chunk['model']
                self.llm_event.prompt = kwargs["messages"]
                choice = chunk['choices'][0]  # NOTE: We assume for completion only choices[0] is relevant

                if choice['delta'].get('content'):
                    accumulated_delta['content'] += choice['delta'].content

                if choice['delta'].get('role'):
                    accumulated_delta['role'] = choice['delta'].get('role')

                if choice['finish_reason']:
                    # Streaming is done. Record LLMEvent
                    self.llm_event.returns.choices[0]['finish_reason'] = choice['finish_reason']
                    self.llm_event.completion = {
                        "role": accumulated_delta['role'], "content": accumulated_delta['content']}
                    self.llm_event.end_timestamp = get_ISO_time()

                    self.client.record(self.llm_event)
            except Exception as e:
                self.client.record(ErrorEvent(trigger_event=self.llm_event, exception=e))
                kwargs_str = pprint.pformat(kwargs)
                chunk = pprint.pformat(chunk)
                logger.warning(
                    f"🖇 AgentOps: Unable to parse a chunk for LLM call. Skipping upload to AgentOps\n"
                    f"chunk:\n {chunk}\n"
                    f"kwargs:\n {kwargs_str}\n"
                )

        # if the response is a generator, decorate the generator
        if inspect.isasyncgen(response):
            async def async_generator():
                async for chunk in response:
                    handle_stream_chunk(chunk)

                    yield chunk
            return async_generator()

        elif inspect.isgenerator(response):
            def generator():
                for chunk in response:
                    handle_stream_chunk(chunk)

                    yield chunk
            return generator()

        self.llm_event = LLMEvent(
            init_timestamp=init_timestamp,
            params=kwargs,
            returns=response
        )
        # v0.0.0 responses are dicts
        try:
            self.llm_event.agent_id = check_call_stack_for_agent_id()
            self.llm_event.prompt = kwargs["messages"]
            self.llm_event.prompt_tokens = response['usage']['prompt_tokens']
            self.llm_event.completion = {"role": "assistant", "content": response['choices'][0]['message']['content']}
            self.llm_event.completion_tokens = response['usage']['completion_tokens']
            self.llm_event.model = response["model"]
            self.llm_event.end_timestamp = get_ISO_time()

            self.client.record(self.llm_event)
        except Exception as e:
            self.client.record(ErrorEvent(trigger_event=self.llm_event, exception=e))
            kwargs_str = pprint.pformat(kwargs)
            response = pprint.pformat(response)
            logger.warning(
                f"🖇 AgentOps: Unable to parse response for LLM call. Skipping upload to AgentOps\n"
                f"response:\n {response}\n"
                f"kwargs:\n {kwargs_str}\n"
            )

        return response

    def _handle_response_v1_openai(self, response, kwargs, init_timestamp):
        """Handle responses for OpenAI versions >v1.0.0"""
        from openai import Stream, AsyncStream
        from openai.types.chat import ChatCompletionChunk
        from openai.resources import AsyncCompletions

        self.llm_event = LLMEvent(
            init_timestamp=init_timestamp,
            params=kwargs
        )

        def handle_stream_chunk(chunk: ChatCompletionChunk):
            # NOTE: prompt/completion usage not returned in response when streaming
            # We take the first ChatCompletionChunk and accumulate the deltas from all subsequent chunks to build one full chat completion
            if self.llm_event.returns == None:
                self.llm_event.returns = chunk

            try:
                accumulated_delta = self.llm_event.returns.choices[0].delta
                self.llm_event.agent_id = check_call_stack_for_agent_id()
                self.llm_event.model = chunk.model
                self.llm_event.prompt = kwargs["messages"]
                choice = chunk.choices[0]  # NOTE: We assume for completion only choices[0] is relevant

                if choice.delta.content:
                    accumulated_delta.content += choice.delta.content

                if choice.delta.role:
                    accumulated_delta.role = choice.delta.role

                if choice.delta.tool_calls:
                    accumulated_delta.tool_calls = choice.delta.tool_calls

                if choice.delta.function_call:
                    accumulated_delta.function_call = choice.delta.function_call

                if choice.finish_reason:
                    # Streaming is done. Record LLMEvent
                    self.llm_event.returns.choices[0].finish_reason = choice.finish_reason
                    self.llm_event.completion = {"role": accumulated_delta.role, "content": accumulated_delta.content,
                                                 "function_call": accumulated_delta.function_call, "tool_calls": accumulated_delta.tool_calls}
                    self.llm_event.end_timestamp = get_ISO_time()

                    self.client.record(self.llm_event)
            except Exception as e:
                self.client.record(ErrorEvent(trigger_event=self.llm_event, exception=e))
                kwargs_str = pprint.pformat(kwargs)
                chunk = pprint.pformat(chunk)
                logger.warning(
                    f"🖇 AgentOps: Unable to parse a chunk for LLM call. Skipping upload to AgentOps\n"
                    f"chunk:\n {chunk}\n"
                    f"kwargs:\n {kwargs_str}\n"
                )

        # if the response is a generator, decorate the generator
        if isinstance(response, Stream):
            def generator():
                for chunk in response:
                    handle_stream_chunk(chunk)
                    yield chunk
            return generator()

        # For asynchronous AsyncStream
        elif isinstance(response, AsyncStream):
            async def async_generator():
                async for chunk in response:
                    handle_stream_chunk(chunk)
                    yield chunk
            return async_generator()

        # For async AsyncCompletion
        elif isinstance(response, AsyncCompletions):
            async def async_generator():
                async for chunk in response:
                    handle_stream_chunk(chunk)
                    yield chunk
            return async_generator()

        self.llm_event = LLMEvent(
            init_timestamp=init_timestamp,
            params=kwargs
        )
        # v1.0.0+ responses are objects
        try:
            self.llm_event.returns = response.model_dump()
            self.llm_event.agent_id = check_call_stack_for_agent_id()
            self.llm_event.prompt = kwargs["messages"]
            self.llm_event.prompt_tokens = response.usage.prompt_tokens
            self.llm_event.completion = response.choices[0].message.model_dump()
            self.llm_event.completion_tokens = response.usage.completion_tokens
            self.llm_event.model = response.model

            self.client.record(self.llm_event)
        except Exception as e:
            self.client.record(ErrorEvent(trigger_event=self.llm_event, exception=e))
            kwargs_str = pprint.pformat(kwargs)
            response = pprint.pformat(response)
            logger.warning(
                f"🖇 AgentOps: Unable to parse response for LLM call. Skipping upload to AgentOps\n"
                f"response:\n {response}\n"
                f"kwargs:\n {kwargs_str}\n"
            )

        return response

    def override_openai_v1_completion(self):
        from openai.resources.chat import completions

        # Store the original method
        original_create = completions.Completions.create

        def patched_function(*args, **kwargs):
            init_timestamp = get_ISO_time()
            # Call the original function with its original arguments
            result = original_create(*args, **kwargs)
            return self._handle_response_v1_openai(result, kwargs, init_timestamp)

      # Override the original method with the patched one
        completions.Completions.create = patched_function

    def override_openai_v1_async_completion(self):
        from openai.resources.chat import completions

        # Store the original method
        original_create = completions.AsyncCompletions.create

        async def patched_function(*args, **kwargs):
            # Call the original function with its original arguments
            init_timestamp = get_ISO_time()
            result = await original_create(*args, **kwargs)
            return self._handle_response_v1_openai(result, kwargs, init_timestamp)

        # Override the original method with the patched one
        completions.AsyncCompletions.create = patched_function

    def override_litellm_completion(self):
        import litellm

        original_create = litellm.completion

        def patched_function(*args, **kwargs):
            init_timestamp = get_ISO_time()
            result = original_create(*args, **kwargs)
            # Note: litellm calls all LLM APIs using the OpenAI format
            return self._handle_response_v1_openai(result, kwargs, init_timestamp)

        litellm.completion = patched_function

    def override_litellm_async_completion(self):
        import litellm

        original_create = litellm.acompletion

        async def patched_function(*args, **kwargs):
            # Call the original function with its original arguments
            init_timestamp = get_ISO_time()
            result = await original_create(*args, **kwargs)
            # Note: litellm calls all LLM APIs using the OpenAI format
            return self._handle_response_v1_openai(result, kwargs, init_timestamp)

        # Override the original method with the patched one
        litellm.acompletion = patched_function

    def _override_method(self, api, method_path, module):
        def handle_response(result, kwargs, init_timestamp):
            if api == "openai":
                return self._handle_response_v0_openai(result, kwargs, init_timestamp)
            return result

        def wrap_method(original_method):
            if inspect.iscoroutinefunction(original_method):
                @functools.wraps(original_method)
                async def async_method(*args, **kwargs):
                    init_timestamp = get_ISO_time()
                    response = await original_method(*args, **kwargs)
                    return handle_response(response, kwargs, init_timestamp)
                return async_method

            else:
                @functools.wraps(original_method)
                def sync_method(*args, **kwargs):
                    init_timestamp = get_ISO_time()
                    response = original_method(*args, **kwargs)
                    return handle_response(response, kwargs, init_timestamp)
                return sync_method

        method_parts = method_path.split(".")
        original_method = functools.reduce(getattr, method_parts, module)
        new_method = wrap_method(original_method)

        if len(method_parts) == 1:
            setattr(module, method_parts[0], new_method)
        else:
            parent = functools.reduce(getattr, method_parts[:-1], module)
            setattr(parent, method_parts[-1], new_method)
