function isQuit = setAllStimRate(stimRate)
%% Constants
NUM_OF_CHANNELS = 16;
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;

%% Function
fprintf('Setting stimulation rate for all channels...');
startTime = tic;
errSetRate = 1;         % initialize errors
while errSetRate ~= 0   % set stimulation rate
    for channelNum = 1:NUM_OF_CHANNELS
        % stimRate_check = [];
        % while ~isequal(stimRate_check,stimRate)
            errSetRate = PS_SetRate(1,channelNum,stimRate);   % setting stimulation rate
        %     [stimRate_check,~] = PS_GetRate(1,channelNum);
        % end
    end
    switch errSetRate                                       % get errors
        case 0                                              % no errors
            stimRate_use = addCommas(stimRate);
            [endTime,unit] = getEndTime(startTime);
            fprintf('%s pps \t\t(%.2f %s)\n',stimRate_use,endTime,unit);% stimulation rate set
        case -1                          % errors found
            msg = 'INVALID ARGUMENT(S)'; % stimulaton rate not set
            fprintf('\n%s\n',msg);
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errSetRate ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('\nQuitting...\n\n');	% quitting
                isQuit = true;
                return;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('\nTrying again...\n\n');% trying again
%                 try
%                     pause(5);
%                     PS_InitAllStim();
%                 catch
%                 end
            otherwise                       % cancel
                fprintf('\nQuitting...\n\n');	% quitting
                isQuit = true;
                return;                     % exit program
        end
    end
end

end