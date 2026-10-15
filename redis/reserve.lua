-- Single Redis EVAL transaction. Three fixed keys: browser count, global count, request ID.
-- ARGV: browser limit, global limit, seconds until JST midnight, request TTL (48h).
-- Return 1=reserved, 0=duplicate, -1=browser limited, -2=global limited.
if redis.call('EXISTS', KEYS[3]) == 1 then return 0 end
local browser = tonumber(redis.call('GET', KEYS[1]) or '0')
local global = tonumber(redis.call('GET', KEYS[2]) or '0')
if browser >= tonumber(ARGV[1]) then return -1 end
if global >= tonumber(ARGV[2]) then return -2 end
redis.call('INCR', KEYS[1])
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
redis.call('SET', KEYS[3], 'reserved', 'EX', tonumber(ARGV[4]))
return 1
