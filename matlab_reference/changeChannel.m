function [...
    channelNum_stop,...
    stopStim,...
    endTime]...
    = changeChannel(...
    multiChannel,...
    channelNum,channelNum_end,...
    startTime,startChannelTime,...
    numOfStim)
%% Constants
TITLE_DONE = 'DONE';
BUTTON_OK = 'OK';
BUTTON_QUIT = 'QUIT';

%% Variables
endTime = [];
stopStim = false;
channelNum_stop = channelNum;
[endChannelTime,unit] = getEndTime(startChannelTime);
nextChannelNum = channelNum + 1;        % next channel number
prompt1 = sprintf('Channel %d completed in %.2f %s\n',channelNum,endChannelTime,unit);	% elapsed time
prompt2 = sprintf('Next is Channel %d.\n',nextChannelNum);                   	% next chnnel
prompt3 = 'Press OK for next channel';                                          % continue

%% Function
if channelNum < channelNum_end          	% more channels
    PS_StopStimChannel(numOfStim,channelNum); % stop stimulating channel
    fprintf(prompt1);
    fprintf(prompt2);
    if ~multiChannel                 	% connected to single channel
        promptDone = {prompt1,prompt2,prompt3};
        questDone = questdlg(promptDone,TITLE_DONE,BUTTON_OK,BUTTON_QUIT);
        switch questDone
            case BUTTON_OK
            case BUTTON_QUIT
                stopStim = true;
            otherwise
                stopStim = true;
        end
    end
%     fprintf('Moving onto next channel...\n\n');% automatically change channel
%     fprintf('\n');
else % on last channel
    fprintf(prompt1);
    [endTime,unit] = getEndTime(startTime);
    fprintf('\nAll stimulations completed in %.2f %s\n',endTime,unit);
end

end