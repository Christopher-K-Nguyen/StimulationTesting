function [quitProgram] = pauseLongTerm(scope,numOfStim,channelSelect,pattern,stimRate)
%% Constants
FIRST = 1;
NUM_OF_PULSES = 0;
MAX_CHANNEL_PER_STIM = 16;
% Buttons
BUTTON_OK = 'Resume';
BUTTON_QUIT = 'Quit';
% Titles
TITLE_PAUSE = 'PAUSE';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Function
fprintf('Pausing...'); % pause setup
fclose(scope);
[quitProgram] = stopStimAllChannel(numOfStim);
fprintf('OK.\n\n');

% Notice
prompt1 = 'Pausing stimulation.';
prompt2 = 'Press "OK" to resume';
prompt3 = 'Check all connections!';
prompt = {prompt1,prompt2,prompt3};
notice = questdlg(prompt,TITLE_PAUSE,BUTTON_OK,BUTTON_QUIT,opts);
switch notice
    case BUTTON_OK
    case BUTTON_QUIT                % quit
        fprintf('Quitting...\n\n'); % quitting
        quitProgram = 1;
        return;                     % exit program
    otherwise                       % cancel
        fprintf('Quitting...\n\n'); % quitting
        quitProgram = 1;
        return;                     % exit program
end

fprintf('Resuming...');
fopen(scope);


for stimNum = FIRST:numOfStim
    if stimNum == FIRST
        channelInStim = find(channelSelect <= MAX_CHANNEL_PER_STIM);
    elseif stimNum == SECOND
         channelInStim = find(channelSelect > MAX_CHANNEL_PER_STIM);
    end
    for channelNum = channelInStim	% consecutively stimulate channels
        if stimNum == SECOND
            channelNum = channelNum - MAX_CHANNEL_PER_STIM;
        end
        % Channel connection
        if multiChannel == NO           % single channel
            channelNum = singleChannel; % PlexStim channel
        end

        % Set rectangular pulse parameters
        [quitProgram] = setStimParam(stimNum,channelNum,pattern);
        if quitProgram == YES
            fclose(scope);
            closeAllStim();
            return;
        end

        % Set number of repetitions for all stimulators
        [quitProgram] = setNumOfPulses(stimNum,channelNum,NUM_OF_PULSES); 
        if quitProgram == YES
            fclose(scope);
            closeAllStim();
            return;
        end

        % Set stimulation rate
        [quitProgram] = setStimRate(stimNum,channelNum,stimRate);
        if quitProgram == YES
            fclose(scope);
            closeAllStim();
            return;
        end
    end
    
    % Load parameters to channel
    [quitProgram] = loadAllChannel(stimNum);
    if quitProgram == YES
        fclose(scope);
        closeAllStim();
        return;
    end
end

% Restart Stimulation
[quitProgram] = startStimAllChannel(numOfStim);
fprintf('OK.\n\n');
end