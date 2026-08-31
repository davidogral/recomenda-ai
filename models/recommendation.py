import random
import json
import os
import pickle
import numpy as np
import pandas as pd
from collections import Counter
from sklearn.metrics.pairwise import cosine_similarity

class MovieRecommender:
    def __init__(self, data_path="data/tmdb_movies_large.json", weights_path="models/weights"):
        self.data_path = data_path
        self.weights_path = weights_path
        self.movies = self.load_movies()
        self.df_movies = pd.DataFrame(self.movies)
        
        # Carregar modelos se existirem
        self.svd_model = self.load_weight("svd_model.pkl")
        self.tfidf_data = self.load_weight("tfidf_model.pkl")
        
        if self.tfidf_data:
            self.tfidf_vectorizer, self.tfidf_matrix = self.tfidf_data
            print("✅ Modelo TF-IDF carregado com sucesso!")
        
        if self.svd_model:
            print("✅ Modelo SVD carregado com sucesso!")

    def load_movies(self):
        try:
            if os.path.exists(self.data_path):
                with open(self.data_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"❌ Erro ao carregar filmes: {e}")
        return []

    def load_weight(self, filename):
        path = os.path.join(self.weights_path, filename)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return pickle.load(f)
        return None

    def get_content_recommendations(self, movie_id, n=10):
        """Recomendação baseada em conteúdo usando TF-IDF"""
        if self.tfidf_data is None:
            return self.get_genre_fallback(movie_id, n)
            
        try:
            idx = self.df_movies[self.df_movies['id'] == movie_id].index[0]
            sim_scores = cosine_similarity(self.tfidf_matrix[idx], self.tfidf_matrix).flatten()
            related_indices = sim_scores.argsort()[::-1][1:n+1]
            
            recommendations = []
            for i in related_indices:
                movie = self.movies[i]
                recommendations.append(self._format_movie(movie, sim_scores[i]))
            return recommendations
        except Exception as e:
            print(f"⚠️ Erro no Content-Based: {e}")
            return self.get_genre_fallback(movie_id, n)

    def recommend_for_user(self, user_id, n=15, selected_movie_ids=None):
        """Recomendação Híbrida"""
        if not selected_movie_ids:
            return self.get_popular_recommendations(n)

        # 1. Pegar recomendações de conteúdo para o último filme selecionado
        last_movie_id = selected_movie_ids[-1]
        content_recs = self.get_content_recommendations(last_movie_id, n=n*2)
        
        # 2. Se tivermos modelo SVD, re-rankear as recomendações de conteúdo
        if self.svd_model:
            final_recs = []
            for rec in content_recs:
                # Predição da nota que este usuário daria para este filme
                pred = self.svd_model.predict(user_id, rec['movie_id']).est
                rec['predicted_rating'] = round(pred, 2)
                final_recs.append(rec)
            
            # Ordenar por nota predita
            final_recs.sort(key=lambda x: x['predicted_rating'], reverse=True)
            return final_recs[:n]
        
        return content_recs[:n]

    def get_genre_fallback(self, movie_id, n=10):
        """Fallback caso os modelos não estejam carregados"""
        selected_genres = []
        for movie in self.movies:
            if movie['id'] == movie_id:
                selected_genres = movie.get('genres', [])
                break
        
        matches = []
        for movie in self.movies:
            if movie['id'] == movie_id: continue
            common = set(selected_genres).intersection(set(movie.get('genres', [])))
            if common:
                matches.append((movie, len(common)))
        
        matches.sort(key=lambda x: x[1], reverse=True)
        return [self._format_movie(m[0], 7.0 + m[1]/10) for m in matches[:n]]

    def get_popular_recommendations(self, n=15):
        movies_copy = self.movies.copy()
        random.shuffle(movies_copy)
        return [self._format_movie(m, m.get('vote_average', 7.5)) for m in movies_copy[:n]]

    def _format_movie(self, movie, score):
        title = movie.get('title', f"Filme {movie['id']}")
        poster = f"https://image.tmdb.org/t/p/w500{movie.get('poster_path', '')}" if movie.get('poster_path') else "https://placehold.co/200x300/141414/FFFFFF?font=roboto&text=Sem+Poster"
        return {
            "movie_id": int(movie['id']),
            "title": title,
            "poster": poster,
            "predicted_rating": float(score)
        }
