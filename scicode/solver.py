"""The supplied Inspect sequential chat protocol, retaining only public prompts."""
from inspect_ai.model import ChatMessageSystem, ChatMessageUser
from inspect_ai.solver import solver
from .dataset import load_records
from .prompt_templates import INITIAL_PROMPT, INITIAL_PROMPT_PROVIDE_BACKGROUND, SUBPROBLEM_PROMPT, SUBPROBLEM_PROMPT_PROVIDE_BACKGROUND


def extract_code(block):
    return block.replace('```python', '').replace('```', '').strip()


def composed_code(record, messages, step):
    codes = [extract_code(m.text) for m in messages if m.role == 'assistant']
    if len(codes) != len(record['sub_steps']):
        raise RuntimeError('Incomplete generation; details withheld.')
    return '\n'.join([record['required_dependencies'],
                      'from test_util import are_dicts_close, cmp_tuple_or_list',
                      *codes[:int(step['step_number'].split('.')[1])]])


@solver
def solve_scicode_problem(provide_scientific_background=False):
    initial = INITIAL_PROMPT_PROVIDE_BACKGROUND if provide_scientific_background else INITIAL_PROMPT
    template = SUBPROBLEM_PROMPT_PROVIDE_BACKGROUND if provide_scientific_background else SUBPROBLEM_PROMPT
    # This factory frame ends before generation. Neither the closure nor any
    # suspended solve frame may retain private records in traceback locals.
    prompts = {
        r['problem_id']: (
            initial.format(required_dependencies=r['required_dependencies']),
            tuple(template.format(**step) for step in r['sub_steps']),
        ) for r in load_records(True)
    }

    async def solve(state, generate):
        system_prompt, step_prompts = prompts[str(state.sample_id)]
        # system_message upstream formats required_dependencies from metadata.
        state.messages.insert(0, ChatMessageSystem(content=system_prompt))
        for prompt in step_prompts:
            state.messages.append(ChatMessageUser(content=prompt))
            state = await generate(state)
        return state
    return solve
