function [...
    channelNum_stop, ...
    isStop, ...
    endTime_cell] ...
    = changeChannel2( ...
    channelNum,channelNum_end, ...
    startTime,startChannelTime)
%% Constants
TITLE_DONE = 'DONE';
BUTTON_OK = 'OK';
BUTTON_QUIT = 'QUIT';

%% Variables
isStop = false;
endTime_cell = {};
channelNum_stop = channelNum;
[endChannelTime,unit] = getEndTime(startChannelTime);
nextChannelNum = channelNum + 1;        % next channel number
prompt1 = sprintf('Channel %d completed in %.2f %s\n',channelNum,endChannelTime,unit);	% elapsed time
prompt2 = sprintf('Next is Channel %d.\n',nextChannelNum);                   	% next chnnel
prompt3 = 'Press OK for next channel';                                          % continue

%% Function
if channelNum < channelNum_end          	% more channels
    PS_StopStimChannel(1,channelNum); % stop stimulating channel
    fprintf(prompt1);
    fprintf(prompt2);
%     fprintf('Moving onto next channel...\n\n');% automatically change channel
%     fprintf('\n');
else % on last channel
    fprintf(prompt1);
    [endTime,unit] = getEndTime(startTime);
    endTime_cell = {endTime,unit};
    fprintf('\nAll stimulations completed in %.2f %s\n',endTime,unit);
end

end