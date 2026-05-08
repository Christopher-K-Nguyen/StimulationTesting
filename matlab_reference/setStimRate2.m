function quitProgram = setStimRate2(stimNum,stimRate)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
channel_arr = 1:16;
quitProgram = false;

%% Function
fprintf('Setting stimulation rate...');
errSetRate = 1;         % initialize errors
while errSetRate ~= 0   % set stimulation rate
    for channelNum = channel_arr
        errSetRate = PS_SetRate(stimNum,channelNum,stimRate);   % setting stimulation rate
    end
    switch errSetRate                                       % get errors
        case 0                                              % no errors
            [rate,~] = PS_GetRate(stimNum,channelNum);
            while rate ~= stimRate
                PS_SetRate(stimNum,channelNum,stimRate);   % setting stimulation rate
                [rate,~] = PS_GetRate(stimNum,channelNum);
            end
            stimRate_use = addCommas(stimRate);
            fprintf('%s pps.\n',stimRate_use);% stimulation rate set
        case -1                          % errors found
            msg = 'INVALID ARGUMENT(S)'; % stimulaton rate not set
            fprintf('\n%s\n',msg);
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errSetRate ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('\nQuitting...\n\n');	% quitting
                quitProgram = true;
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
                quitProgram = true;
                return;                     % exit program
        end
    end
end

end