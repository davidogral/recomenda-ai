import json
import os

def load_movie_names(data_dir="data"):
    """Carrega dicionário de filmes do TMDB (em português)"""
    file_path = os.path.join(data_dir, "tmdb_movies.json")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            movies = json.load(f)
        
        movie_dict = {}
        for movie in movies:
            movie_dict[movie['id']] = movie['title']
        
        return movie_dict
    except FileNotFoundError:
        print("❌ Arquivo tmdb_movies.json não encontrado!")
        return {}

def load_movies_data(data_dir="data"):
    """Carrega dados completos dos filmes"""
    file_path = os.path.join(data_dir, "tmdb_movies.json")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ Arquivo tmdb_movies.json não encontrado!")
        return []