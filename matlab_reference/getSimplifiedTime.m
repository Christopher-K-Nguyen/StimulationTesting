function [time_use] = getSimplifiedTime(time)
%% Function
time_s = time;
time_min = time_s / 60;
time_h = time_s / 3600;
if time_min < 1
    time_use = sprintf('%f s',time_s);
elseif time_h < 1
    time_use = sprintf('%f min',time_min);
else
    time_use = sprintf('%f h',time_h);
end

end