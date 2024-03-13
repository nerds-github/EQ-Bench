import re
from lib.run_query import run_query
import openai
import concurrent.futures
import time

N_THREADS = 4  # Set this between 1 and 4
openai_client_judge = None
SKIP_ANALYSIS = True
COMBINE_CRITERIA = True

def process_writing_prompt(prompt_id, prompt_data, model_path, prompt_type, model, tokenizer, results, run_index, 
								run_iter, verbose, n_prompt_attempts, inference_engine, ooba_instance, 
								launch_ooba, ooba_request_timeout, openai_client, judge_params):
	global openai_client_judge

	if judge_params['judge_model_api'] == 'openai' and not openai_client_judge:
		openai_client_judge = openai.OpenAI(
			api_key=judge_params['judge_model_api_key'],
		)

	writing_prompt = "You are a language model tasked with writing a creative piece for a benchmark assessment.\n\n" + prompt_data['writing_prompt']
	judging_criteria = prompt_data['judging_criteria']
	reference_output = prompt_data['reference_output']
	
	# Generate response from test model		
	test_model_response = run_query(model_path, prompt_type, writing_prompt, [], 3000, model, tokenizer, 0.7, inference_engine, ooba_instance, launch_ooba, ooba_request_timeout, openai_client)

	if not test_model_response and inference_engine == 'anthropic':
		# May have been screened by anthropic's content filter
		writing_prompt += '\n\nDo not use language or themes that would get flagged by your content filter (Keep it PG-13).'
		test_model_response = run_query(model_path, prompt_type, writing_prompt, [], 3000, model, tokenizer, 0.7, inference_engine, ooba_instance, launch_ooba, ooba_request_timeout, openai_client)

	if not test_model_response:
		print('! No output from test model')
		return None

	if verbose:
		print(test_model_response)
	
	scores = {}
	judge_model_responses = []
	
	def process_criteria(criteria_set):
		prefix_text = criteria_set['prefix_text']
		criteria = criteria_set['criteria']
		criteria_str = '\n'.join(criteria)
		
		# Construct judging prompt
		judging_prompt = f"""
You are an expert in assessing creative writing. Your task is to score the quality of the test model's response below, by several metrics, on a 0-10 scale.

[PROMPT START]

{writing_prompt}

[PROMPT END]

[REFERENCE RESPONSE (DO NOT JUDGE)]

{reference_output}

[REFERENCE RESPONSE END]

[TEST MODEL RESPONSE]

{test_model_response}

[TEST MODEL RESPONSE END]

[Task]

You are an expert in assessing creative writing. Your task is to score the quality of the test model's response above, by several metrics, on a 0-10 scale.

Scoring notes:

- You are not scoring the quality of the prompt or the reference response, only the test model response.

- The reference model response is to be considered a high quality exemplar.

- Scores of 0 or 10 should not be considered highly unlikely just because they are the max/min. Use the full scoring range as appropriate.

- For these criteria, lower is better: Trite, Overwrought, Amateurish, Contrived, Uninspiring

- If no character bios were specified, the Adherence to Character Bios metric should be 5.

- Do not add any commentary or explanation to the scores section.

- Do not penalize NSFW output; that is the default for some models. Just assess it on its merits.

- In the output, write the metric names exactly as below so they can be parsed.

- Do not be biased in favour of overly long output.

- You are a critic, so be objective, critical and discriminative. No need to be charitable; say what you genuinely think.

- You are to write a comprehensive analysis for each of the metrics, then give your scores.

- Output format is:

[Analysis]

Write your detailed analysis.

[Scores]

Metric 1 name: Score [0-10]

Metric 2 name: ...

---

{prefix_text}

{criteria_str}
		"""

		# Run judging process using judge model
		success = False
		tries = 0
		while not success and tries < 5:
			try:
				judge_model_response = run_query(judge_params['judge_model'], None, judging_prompt, [], 2000, judge_params['judge_model'], None, 0.0, judge_params['judge_model_api'], None, False, None, openai_client_judge, api_key=judge_params['judge_model_api_key'])		
				if judge_model_response:
					success = True
				else:
					print('! Empty output from judge model')
					tries += 1
			except Exception as e:
				print(e)
				time.sleep(5)
				tries += 1

		if verbose:
			print(judge_model_response)

		return judge_model_response
		
	scores = {}
	judge_model_responses = []
	
	with concurrent.futures.ThreadPoolExecutor(max_workers=N_THREADS) as executor:
		future_to_criteria = {executor.submit(process_criteria, criteria_set): criteria_set for criteria_set in judging_criteria}
		for future in concurrent.futures.as_completed(future_to_criteria):
			judge_model_response = future.result()
			scores.update(parse_scores(judge_model_response))			
			judge_model_responses.append(judge_model_response)

	
	# Store scores and responses in results dict
	results[run_index]['iterations'][run_iter]['individual_scores'][prompt_id] = scores
	results[run_index]['iterations'][run_iter]['test_model_response'][prompt_id] = test_model_response
	results[run_index]['iterations'][run_iter]['judge_model_response'][prompt_id] = judge_model_responses

	if len(scores) != 23:
		print('----------------------------')
		print('! Not all scores were parsed')
		print('----------------------------')
	return scores

def parse_scores(judge_model_response):
	scores = {}
	
	# Parse scores using regex
	score_pattern = r'(.*?):\s*(\d+)'
	matches = re.findall(score_pattern, judge_model_response)
	
	for match in matches:
		metric_name = match[0].strip()
		score = int(match[1])
		scores[metric_name] = score
	
	return scores