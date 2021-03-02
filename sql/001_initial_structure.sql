create table link_tokens
(
	discord_nick varchar(191) not null,
	discord_uid int not null
		constraint link_tokens_pk
			primary key,
	token varchar(191) not null,
	valid_until int not null,
	avatar_url text
);

create unique index link_tokens_discord_uid_uindex
	on link_tokens (discord_uid);

create unique index link_tokens_token_uindex
	on link_tokens (token);



create table spotify_details
(
	discord_uid int not null
		constraint spotify_details_pk
			primary key,
	username text,
	oauth_refresh text
);

create unique index spotify_details_discord_uid_uindex
	on spotify_details (discord_uid);

create unique index spotify_details_username_uindex
	on spotify_details (username);



update meta set value = "1" where key = "db_version";
