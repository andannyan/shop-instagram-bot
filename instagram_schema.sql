-- Instagram Content Themes Table
CREATE TABLE IF NOT EXISTS instagram_content (
  id BIGSERIAL PRIMARY KEY,
  theme VARCHAR(100) NOT NULL,
  description TEXT NOT NULL,
  prompt TEXT NOT NULL,
  category VARCHAR(50),
  hashtags VARCHAR(500),
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Instagram Posts Log Table
CREATE TABLE IF NOT EXISTS instagram_posts_log (
  id BIGSERIAL PRIMARY KEY,
  theme_id BIGINT REFERENCES instagram_content(id),
  caption TEXT NOT NULL,
  image_url VARCHAR(500),
  instagram_post_id VARCHAR(100),
  posted_at TIMESTAMP WITH TIME ZONE,
  status VARCHAR(50) DEFAULT 'published',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Примеры тем (12 для стартера)
INSERT INTO instagram_content (theme, description, prompt, category, hashtags) VALUES
('Sativa Morning Vibes', 'Утренний энергетик с видом на пляж', 'Cannabis sativa plant in morning sunlight on Koh Samui beach with coffee cup', 'sativa', '#SamuiCannabis #MorningHigh #BeachVibes #KohSamui'),
('Indica Chill Sunset', 'Вечерний релакс на закате', 'Premium cannabis indica buds with sunset ocean view, relaxing atmosphere', 'indica', '#IndicaRelax #SunsetKohSamui #ChillVibes #CannabisIsland'),
('Hybrid Balance', 'Идеальный баланс для любого времени', 'Hybrid cannabis strain with tropical flowers and balanced energy', 'hybrid', '#HybridHigh #BalancedCannabis #KohSamui'),
('Tropical Flower', 'Красивые цветущие шишки', 'Close-up of cannabis flowers with tropical background, crystalline trichomes', 'sativa', '#CannabisCulture #TropicalVibes #Cannabisflowers'),
('Beach & Smoke', 'Идеально для пляжного дня', 'Cannabis user enjoying sunset on Koh Samui private beach', 'hybrid', '#BeachLife #KohSamui #CannabisParadise'),
('Rolling Art', 'Мастерство в катании', 'Perfectly rolled cannabis joint on wooden table with tropical decoration', 'indica', '#RollingArt #PerfectJoint #CannabisLife'),
('Green Gold', 'Премиум качество', 'Highest quality cannabis buds with golden-green color', 'sativa', '#PremiumCannabis #QualityMatters #KohSamui'),
('Tropical Paradise', 'Рай на земле', 'Cannabis plants in tropical garden setting with Koh Samui landscape', 'hybrid', '#TropicalParadise #CannabisGarden #IslandLife'),
('Night Relaxation', 'Спокойная ночь', 'Cannabis indica for sleep, cozy bedroom setting with ocean sounds', 'indica', '#SleepWell #IndicaNight #RelaxMode'),
('Fresh Pick', 'Свежий урожай', 'Fresh cannabis buds just harvested, morning dew', 'sativa', '#FreshPick #CannabisHarvest #QualityCheck'),
('Island Strain', 'Местный сорт', 'Locally grown Koh Samui cannabis with unique tropical characteristics', 'hybrid', '#LocalStrain #IslandGrown #SupportLocal'),
('Golden Hour', 'Волшебный час', 'Cannabis with golden hour lighting, perfect for photography', 'sativa', '#GoldenHour #CannabisPhotography #MagicTime');
