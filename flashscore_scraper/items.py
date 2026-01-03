import scrapy

class MatchItem(scrapy.Item):
    home_team = scrapy.Field()
    away_team = scrapy.Field()
    date = scrapy.Field()
    match_id = scrapy.Field()
    home_score = scrapy.Field()
    away_score = scrapy.Field()
    
    # Odds
    interaction_1x2_1 = scrapy.Field()
    interaction_1x2_X = scrapy.Field()
    interaction_1x2_2 = scrapy.Field()
    
    over_2_5 = scrapy.Field()
    under_2_5 = scrapy.Field()
    start_time = scrapy.Field()
    
    # H2H Data (List of Dicts: date, opponent, score, outcome)
    last_matches_home = scrapy.Field()
    last_matches_away = scrapy.Field()
    
    # League Info
    league = scrapy.Field()
    
    # Internal
    base_url = scrapy.Field()


