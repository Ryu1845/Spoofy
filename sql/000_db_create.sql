create table meta
(
    key   varchar(191) not null
        constraint meta_pk
            primary key,
    value varchar(191) not null
);

create unique index meta_key_uindex
    on meta (key);



insert into meta ("key", "value") values ("db_version", "0");
insert into meta ("key", "value") values ("web_base_url", "https://spoofy.baka.tokyo");
insert into meta ("key", "value") values ("stream_host", "spoofy.baka.tokyo");
insert into meta ("key", "value") values ("playlist_account_uid", "1");
