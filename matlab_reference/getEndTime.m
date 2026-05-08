function [endTime,unit] = getEndTime(startTime)

endTime_s = toc(startTime);
endTime_ms = endTime_s * 1e3;
endTime_us = endTime_ms * 1e3;
endTime_min = endTime_s / 60;
endTime_h = endTime_min / 60;

if endTime_ms < 0.1
    endTime = endTime_us;
    unit = 'us';
elseif endTime_s < 0.1
    endTime = endTime_ms;
    unit = 'ms';
elseif endTime_s < 60
    endTime = endTime_s;
    unit = 's';
elseif endTime_min < 60
    endTime = endTime_min;
    unit = 'min';
else
    endTime = endTime_h;
    unit = 'h';
end

end