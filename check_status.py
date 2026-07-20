import requests, sys, time

token = sys.argv[1]
headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}

time.sleep(15)
r = requests.get('https://api.github.com/repos/viktorlaw-dev/daily-perp-signals/actions/runs', headers=headers, params={'per_page': 1, 'status': 'completed'})
data = r.json()
if data.get('workflow_runs'):
    run = data['workflow_runs'][0]
    print(f"Run #{run['run_number']} - {run['conclusion']} - {run['html_url']}")
else:
    print('No completed runs found')
