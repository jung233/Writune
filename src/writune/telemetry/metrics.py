"""Report costs only when explicit prices and actual usage exist."""
import math

def configured_cost(row,settings):
    if row.get('measured_usage_requests',row.get('requests'))!=row.get('requests'):
        return None
    provider=settings.providers.get(row['provider'])
    if provider is None:
        return None
    prices=provider.prices.get(row['model'],{})
    models=getattr(provider,'models',{})
    configured=models.get(row['model']) if isinstance(models,dict) else None
    if configured:
        prices=getattr(configured,'prices',None) or getattr(configured,'price',None) or prices
    if not isinstance(prices,dict):
        return None
    required=('input_per_million','output_per_million')
    if any(k not in prices for k in required) or row['input_tokens'] is None or row['output_tokens'] is None:
        return None
    try:
        rates=[float(prices[k]) for k in required]
        cached_rate=float(prices.get('cached_input_per_million',rates[0]))
    except (ValueError,TypeError):
        return None
    if any(not math.isfinite(x) or x<0 for x in [*rates,cached_rate]):
        return None
    cached=min(row['input_tokens'],row['cached_input_tokens'] or 0)
    value=((row['input_tokens']-cached)*rates[0]+cached*cached_rate+row['output_tokens']*rates[1])/1_000_000
    return {'amount':round(value,10),'currency':str(prices.get('currency','USD')),'basis':'configured_prices_and_reported_usage'}
